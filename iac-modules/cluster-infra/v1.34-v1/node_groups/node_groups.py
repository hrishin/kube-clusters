"""
EKS Node Groups infrastructure
Equivalent to terraform/modules/node-groups
"""

import base64
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError  # type: ignore

from kubernetes import client as k8s_client  # type: ignore
from kubernetes.client import rest as k8s_rest  # type: ignore

import pulumi
import pulumi_aws as aws
from botocore.credentials import Credentials
from botocore.model import ServiceId
from botocore.signers import RequestSigner


class WaitForAsgReady(pulumi.ComponentResource):
    ready: pulumi.Output[bool]

    def __init__(
        self,
        name: str,
        asg_name: pulumi.Input[str],
        region: pulumi.Input[str],
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 15,
        cluster_name: Optional[pulumi.Input[str]] = None,
        cluster_endpoint: Optional[pulumi.Input[str]] = None,
        cluster_ca_data: Optional[pulumi.Input[str]] = None,
        node_group_label: Optional[str] = None,
        opts: Optional[pulumi.ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:autoscaling:WaitForAsgReady", name, None, opts)

        readiness_details = pulumi.Output.all(
            asg_name,
            region,
            timeout_seconds,
            poll_interval_seconds,
            cluster_name,
            cluster_endpoint,
            cluster_ca_data,
            node_group_label,
        ).apply(self._wait_for_asg_ready)

        self.ready = readiness_details.apply(lambda _: True)
        self.details = readiness_details

        self.register_outputs(
            {
                "ready": self.ready,
                "details": self.details,
            }
        )

    def _wait_for_asg_ready(
        self,
        args: Tuple[
            str,
            str,
            int,
            int,
            Optional[str],
            Optional[str],
            Optional[str],
            Optional[str],
        ],
    ) -> Dict[str, Any]:
        (
            asg_name,
            region,
            timeout_seconds,
            poll_interval_seconds,
            cluster_name,
            cluster_endpoint,
            cluster_ca_data,
            node_group_label,
        ) = args

        if pulumi.runtime.is_dry_run():
            pulumi.log.info(
                f"Preview: skipping Auto Scaling Group readiness check for {asg_name or '<unknown>'}."
            )
            return {
                "asg_name": asg_name,
                "desired_capacity": 0,
                "healthy_instances": 0,
            }

        client = boto3.client("autoscaling", region_name=region)
        deadline = time.time() + timeout_seconds

        # Phase 1: wait for any active instance refresh to complete.
        # Without this, the health check below would pass immediately on old instances
        # while the rolling replacement is still in progress.
        self._wait_for_instance_refresh(client, asg_name, deadline, poll_interval_seconds)

        # Phase 2: confirm all instances are healthy after the refresh.
        while time.time() < deadline:
            try:
                response = client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
            except ClientError as exc:
                pulumi.log.warn(f"Failed to describe Auto Scaling Group {asg_name}: {exc}")
                time.sleep(poll_interval_seconds)
                continue

            groups = response.get("AutoScalingGroups") or []
            if not groups:
                pulumi.log.warn(f"Auto Scaling Group {asg_name} not found; retrying...")
                time.sleep(poll_interval_seconds)
                continue

            group = groups[0]
            desired_capacity = group.get("DesiredCapacity", 0)
            instances = group.get("Instances") or []
            pulumi.log.info(f"Auto Scaling Group {asg_name} has {desired_capacity} desired capacity and {len(instances)} instances.")
            for instance in instances:
                pulumi.log.info(f"Instance {instance.get('InstanceId')} is {instance.get('LifecycleState')} and {instance.get('HealthStatus')}.")
            healthy_instances = sum(
                1
                for instance in instances
                if instance.get("LifecycleState") == "InService" and instance.get("HealthStatus") == "Healthy"
            )

            if desired_capacity == 0 or healthy_instances >= desired_capacity:
                pulumi.log.info(
                    f"Auto Scaling Group {asg_name} is healthy ({healthy_instances}/{desired_capacity} instances InService)."
                )
                kubernetes_details: Dict[str, Any] = {}
                if (
                    desired_capacity > 0
                    and cluster_name
                    and cluster_endpoint
                    and cluster_ca_data
                    and node_group_label
                ):
                    try:
                        kubernetes_details = self._wait_for_kubernetes_nodes_ready(
                            cluster_name=cluster_name,
                            cluster_endpoint=cluster_endpoint,
                            cluster_ca_data=cluster_ca_data,
                            node_group_label=node_group_label,
                            desired_capacity=desired_capacity,
                            region=region,
                            timeout_seconds=max(int(deadline - time.time()), 60),
                            poll_interval_seconds=poll_interval_seconds,
                        )
                    except Exception as exc:  # noqa: BLE001
                        pulumi.log.error(
                            f"Kubernetes readiness check for node group {node_group_label} failed: {exc}"
                        )
                        raise Exception(
                            f"Kubernetes readiness check for node group {node_group_label} failed"
                        ) from exc
                else:
                    if desired_capacity == 0:
                        pulumi.log.info(
                            f"Node group {node_group_label or '<unknown>'} has desired capacity 0; skipping Kubernetes readiness wait."
                        )
                    else:
                        pulumi.log.info(
                            f"Skipping Kubernetes readiness wait for node group {node_group_label or '<unknown>'} due to missing cluster context."
                        )

                return {
                    "asg_name": asg_name,
                    "desired_capacity": desired_capacity,
                    "healthy_instances": healthy_instances,
                    "kubernetes": kubernetes_details or None,
                }
            pulumi.log.info(f"Auto Scaling Group {asg_name} is not healthy ({healthy_instances}/{desired_capacity} instances InService). Retrying...")
            time.sleep(poll_interval_seconds)

        raise Exception(
            f"Timed out after {timeout_seconds}s waiting for Auto Scaling Group {asg_name} to become healthy."
        )

    def _wait_for_instance_refresh(
        self,
        client: Any,
        asg_name: str,
        deadline: float,
        poll_interval_seconds: int,
    ) -> None:
        """Block until no instance refresh is InProgress or Pending for this ASG."""
        active_statuses = {"InProgress", "Pending", "Cancelling"}
        while time.time() < deadline:
            try:
                resp = client.describe_instance_refreshes(AutoScalingGroupName=asg_name)
            except ClientError as exc:
                pulumi.log.warn(f"Failed to describe instance refreshes for {asg_name}: {exc}")
                time.sleep(poll_interval_seconds)
                continue

            active = [
                r for r in (resp.get("InstanceRefreshes") or [])
                if r.get("Status") in active_statuses
            ]
            if not active:
                return  # no active refresh — done

            r = active[0]
            status = r.get("Status", "Unknown")
            pct = r.get("PercentageComplete", 0)
            pulumi.log.info(
                f"Instance refresh {r.get('InstanceRefreshId', '')} for {asg_name}: "
                f"{status} ({pct}% complete)"
            )
            time.sleep(poll_interval_seconds)

        raise Exception(
            f"Timed out after waiting for instance refresh on {asg_name} to complete."
        )

    def _wait_for_kubernetes_nodes_ready(
        self,
        cluster_name: str,
        cluster_endpoint: str,
        cluster_ca_data: str,
        node_group_label: str,
        desired_capacity: int,
        region: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
    ) -> Dict[str, Any]:
        kubernetes_timeout = timeout_seconds
        deadline = time.time() + kubernetes_timeout

        ca_file_path = self._write_ca_file(cluster_ca_data)
        try:
            last_error: Optional[str] = None

            while time.time() < deadline:
                token = self._generate_bearer_token(cluster_name, region)

                configuration = k8s_client.Configuration()
                configuration.host = cluster_endpoint
                configuration.verify_ssl = True
                configuration.ssl_ca_cert = ca_file_path
                configuration.api_key_prefix = {"authorization": "Bearer"}
                configuration.api_key = {"authorization": token}

                try:
                    with k8s_client.ApiClient(configuration) as api_client:
                        core_api = k8s_client.CoreV1Api(api_client)
                        response = core_api.list_node(
                            label_selector=f"NodeGroup={node_group_label}",
                            _request_timeout=30,
                        )
                except k8s_rest.ApiException as exc:
                    last_error = f"HTTP {exc.status}"
                    pulumi.log.warn(
                        f"Kubernetes API returned status {exc.status} for node group {node_group_label}. Body: {exc.body}"
                    )
                    time.sleep(poll_interval_seconds)
                    continue
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    pulumi.log.warn(
                        f"Error querying Kubernetes API for node group {node_group_label}: {exc}. Retrying..."
                    )
                    time.sleep(poll_interval_seconds)
                    continue

                items = response.items or []
                ready_nodes = 0

                for node in items:
                    if isinstance(node, dict):
                        node_status = node.get("status", {})
                        conditions = node_status.get("conditions", []) or []
                    else:
                        node_status = getattr(node, "status", None)
                        conditions = getattr(node_status, "conditions", []) or []

                    for condition in conditions:
                        if isinstance(condition, dict):
                            condition_type = condition.get("type")
                            condition_status = condition.get("status")
                        else:
                            condition_type = getattr(condition, "type", None)
                            condition_status = getattr(condition, "status", None)

                        if condition_type == "Ready" and condition_status == "True":
                            ready_nodes += 1
                            break

                pulumi.log.info(
                    f"Kubernetes reports {ready_nodes}/{desired_capacity} Ready nodes for node group {node_group_label}."
                )

                if ready_nodes >= desired_capacity:
                    pulumi.log.info(
                        f"All nodes for group {node_group_label} report Ready status in Kubernetes."
                    )
                    return {
                        "node_group": node_group_label,
                        "ready_nodes": ready_nodes,
                        "expected_ready_nodes": desired_capacity,
                        "status": "ready",
                    }

                time.sleep(poll_interval_seconds)

            raise Exception(
                f"Timed out after {kubernetes_timeout}s waiting for Kubernetes nodes in group {node_group_label} to become Ready. "
                f"Last seen error: {last_error or 'none'}"
            )
        finally:
            if ca_file_path and os.path.exists(ca_file_path):
                try:
                    os.remove(ca_file_path)
                except OSError:
                    pass

    def _write_ca_file(self, cluster_ca_data: str) -> str:
        decoded = base64.b64decode(cluster_ca_data)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(decoded)
            return tmp.name

    def _generate_bearer_token(self, cluster_name: str, region: str) -> str:
        import base64 as _base64

        session = self._get_botocore_session(region)
        credentials = session.get_credentials()
        if credentials is None:
            raise Exception("Unable to find AWS credentials for Kubernetes readiness check.")

        if not hasattr(credentials, "get_frozen_credentials"):
            credentials = Credentials(
                access_key=credentials.access_key,
                secret_key=credentials.secret_key,
                token=getattr(credentials, "token", None),
                account_id=getattr(credentials, "account_id", None),
            )
        signer = RequestSigner(
            service_id=ServiceId("sts"),
            region_name=region,
            signing_name="sts",
            signature_version="v4",
            credentials=credentials,
            event_emitter=session.get_component("event_emitter"),
        )

        params = {
            "method": "GET",
            "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
            "body": {},
            "headers": {"x-k8s-aws-id": cluster_name},
            "context": {},
        }

        signed_url = signer.generate_presigned_url(
            params, region_name=region, expires_in=60, operation_name=""
        )
        token = (
            "k8s-aws-v1."
            + _base64.urlsafe_b64encode(signed_url.encode("utf-8"))
            .decode("utf-8")
            .rstrip("=")
        )
        return token

    @staticmethod
    def _get_botocore_session(region: str):
        import botocore.session

        session = botocore.session.get_session()
        if not session.get_config_variable("region"):
            session.set_config_variable("region", region)
        return session


def create_node_groups(
    cluster_name: str,
    node_groups: Dict[str, Any],
    node_group_service_role_arn: pulumi.Output,
    node_instance_profile_name: pulumi.Output,
    cluster_security_group_id: pulumi.Output,
    worker_node_security_group_id: pulumi.Output,
    private_subnet_ids: List[pulumi.Output],
    cluster_endpoint: pulumi.Output,
    cluster_ca_data: pulumi.Output,
    tags: Dict[str, str],
    region: str,
    dns_cluster_ip: Optional[pulumi.Input[str]] = None,
) -> Dict[str, Any]:
    """
    Create self-managed node groups with launch templates and auto-scaling groups
    
    Args:
        cluster_name: Name of the EKS cluster
        node_groups: Node groups configuration from YAML
        node_group_service_role_arn: IAM role ARN for node groups
        node_instance_profile_name: Instance profile name
        cluster_security_group_id: Cluster security group ID
        worker_node_security_group_id: Worker node security group ID
        private_subnet_ids: List of private subnet IDs
        cluster_endpoint: EKS cluster endpoint
        cluster_ca_data: Cluster CA certificate data
        tags: Common tags to apply to resources
        region: AWS region where the Auto Scaling Groups are created
        dns_cluster_ip: Cluster IP address for CoreDNS service to pass to bootstrap
        
    Returns:
        Dictionary containing node group resource outputs
    """
    
    # Taint effect mapping
    taint_effect_map = {
        "NoSchedule": "NO_SCHEDULE",
        "NoExecute": "NO_EXECUTE",
        "PreferNoSchedule": "PREFER_NO_SCHEDULE",
    }
    
    # Detect GPU type based on instance type
    def detect_gpu_type(instance_types: List[str]) -> Dict[str, str]:
        """Detect GPU type from instance type"""
        instance_type = instance_types[0] if instance_types else ""
        
        if instance_type.startswith("p3"):
            return {"accelerator": "nvidia-gpu", "nvidia.com/gpu": "true", "gpu-type": "tesla-v100"}
        elif instance_type.startswith("p4"):
            return {"accelerator": "nvidia-gpu", "nvidia.com/gpu": "true", "gpu-type": "a100"}
        elif instance_type.startswith("g4"):
            return {"accelerator": "nvidia-gpu", "nvidia.com/gpu": "true", "gpu-type": "t4"}
        elif instance_type.startswith("g5"):
            return {"accelerator": "nvidia-gpu", "nvidia.com/gpu": "true", "gpu-type": "a10g"}
        elif instance_type.startswith("g6"):
            return {"accelerator": "nvidia-gpu", "nvidia.com/gpu": "true", "gpu-type": "l4"}
        else:
            return {}
    
    # Get all private subnet details for AZ filtering
    subnet_details = {}
    for subnet_id in private_subnet_ids:
        # We'll filter subnets later based on AZ requirements
        pass
    
    launch_templates: Dict[str, aws.ec2.LaunchTemplate] = {}
    autoscaling_groups: Dict[str, aws.autoscaling.Group] = {}
    asg_readiness_checks: Dict[str, WaitForAsgReady] = {}
    
    for ng_name, ng_config in node_groups.items():
        pulumi.log.info("Creating node group infrastructure...")
        pulumi.log.info(f"Node group name: {ng_name}")
        pulumi.log.info(f"Node group config: {ng_config}")
        
        # Process labels
        base_labels = ng_config.get("labels", {})
        gpu_labels = detect_gpu_type(ng_config.get("instance_types", ["t3.medium"]))
        # Ensure each node registers with a NodeGroup label so readiness checks can find it.
        system_labels = {"NodeGroup": ng_name}
        all_labels = {**system_labels, **base_labels, **gpu_labels}
        
        # Process taints
        taints = ng_config.get("taints", [])
        
        # Create user data script
        max_pods = ng_config.get("max_pods")

        extra_kubelet_feature_gates = ng_config.get("kubelet_feature_gates", {})

        def create_user_data(endpoint: str, ca_data: str, labels: Dict[str, str], taints_list: List[Dict[str, str]], dns_ip: Optional[str]) -> str:
            labels_str = ",".join([f"{k}={v}" for k, v in labels.items()])
            taints_str = ",".join([
                f"{t['key']}={t.get('value', '')}:{t['effect']}"
                for t in taints_list
            ])

            kubelet_args = ""
            if labels_str:
                kubelet_args += f" --node-labels={labels_str}"
            if taints_str:
                kubelet_args += f" --register-with-taints={taints_str}"
            if max_pods is not None:
                kubelet_args += f" --max-pods={max_pods}"

            lines = [
                "#!/bin/bash",
                "set -o xtrace",
                f"/etc/eks/bootstrap.sh {cluster_name} \\",
                f"  --apiserver-endpoint '{endpoint}' \\",
                f"  --b64-cluster-ca '{ca_data}' \\",
                f"  --use-max-pods false \\",
            ]
            if dns_ip:
                lines.append(f"  --dns-cluster-ip '{dns_ip}' \\")
            lines.append(f'  --kubelet-extra-args "{kubelet_args}"')

            # Patch kubelet config with extra feature gates (e.g. DynamicResourceAllocation for DRA/GPU).
            # bootstrap.sh writes kubelet-config.json and starts the kubelet; we patch then restart.
            if extra_kubelet_feature_gates:
                gate_updates = "\n".join(
                    f"cfg['featureGates']['{k}'] = {'True' if v else 'False'}"
                    for k, v in extra_kubelet_feature_gates.items()
                )
                lines += [
                    "",
                    "# Patch kubelet feature gates not set by EKS bootstrap",
                    "until [ -f /etc/kubernetes/kubelet/kubelet-config.json ]; do sleep 1; done",
                    "python3 << 'PYEOF'",
                    "import json",
                    "with open('/etc/kubernetes/kubelet/kubelet-config.json') as f:",
                    "    cfg = json.load(f)",
                    "cfg.setdefault('featureGates', {})",
                    gate_updates,
                    "with open('/etc/kubernetes/kubelet/kubelet-config.json', 'w') as f:",
                    "    json.dump(cfg, f, indent=4)",
                    "PYEOF",
                    "systemctl restart snap.kubelet-eks.daemon.service",
                ]

            return "\n".join(lines) + "\n"
        
        if dns_cluster_ip is not None:
            user_data = pulumi.Output.all(cluster_endpoint, cluster_ca_data, all_labels, taints, dns_cluster_ip).apply(
                lambda args: base64.b64encode(create_user_data(args[0], args[1], args[2], args[3], args[4]).encode()).decode()
            )
        else:
            user_data = pulumi.Output.all(cluster_endpoint, cluster_ca_data, all_labels, taints).apply(
                lambda args: base64.b64encode(create_user_data(args[0], args[1], args[2], args[3], None).encode()).decode()
            )
        
        # Combine security groups
        security_group_ids = pulumi.Output.all(
            cluster_security_group_id,
            worker_node_security_group_id
        ).apply(lambda args: [args[0], args[1]])
        
        # Create Launch Template
        lt = aws.ec2.LaunchTemplate(
            f"{cluster_name}_{ng_name}_lt",
            name_prefix=f"{cluster_name}_{ng_name}_",
            vpc_security_group_ids=security_group_ids,
            image_id=ng_config.get("ami_id"),
            iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
                name=node_instance_profile_name,
            ),
            instance_type=ng_config.get("instance_types", ["t3.medium"])[0],
            block_device_mappings=[
                aws.ec2.LaunchTemplateBlockDeviceMappingArgs(
                    device_name="/dev/xvda",
                    ebs=aws.ec2.LaunchTemplateBlockDeviceMappingEbsArgs(
                        volume_size=ng_config.get("disk_size", 20),
                        volume_type="gp3",
                        delete_on_termination=True,
                    ),
                ),
            ],
            metadata_options=aws.ec2.LaunchTemplateMetadataOptionsArgs(
                http_endpoint="enabled",
                http_tokens="required",
                http_put_response_hop_limit=2,
            ),
            placement=aws.ec2.LaunchTemplatePlacementArgs(
                tenancy=ng_config.get("tenancy")
            ) if ng_config.get("tenancy") else None,
            user_data=user_data,
            tag_specifications=[
                aws.ec2.LaunchTemplateTagSpecificationArgs(
                    resource_type="instance",
                    tags={
                        **tags,
                        "Name": f"{cluster_name}-{ng_name}-node",
                        "NodeGroup": ng_name,
                        f"kubernetes.io/cluster/{cluster_name}": "owned",
                    },
                ),
                aws.ec2.LaunchTemplateTagSpecificationArgs(
                    resource_type="volume",
                    tags={
                        **tags,
                        "Name": f"{cluster_name}-{ng_name}-volume",
                        "NodeGroup": ng_name,
                    },
                ),
            ],
        )
        
        launch_templates[ng_name] = lt
        
        # Determine subnets for this node group
        # If availability_zones is specified, filter subnets
        # For now, we'll use all private subnets
        # In production, you'd need to filter based on AZ
        ng_subnet_ids = private_subnet_ids
        
        # If availability_zones is specified in config, we need to filter
        # This would require looking up subnet AZs, which we'll handle with Output.all
        if ng_config.get("availability_zones"):
            # For simplicity, using all subnets. In production, filter by AZ
            pass
        
        # Create Auto Scaling Group
        asg = aws.autoscaling.Group(
            f"{cluster_name}-{ng_name}-asg",
            name=f"{cluster_name}-{ng_name}",
            vpc_zone_identifiers=ng_subnet_ids,
            desired_capacity=ng_config.get("desired_size", 1),
            max_size=ng_config.get("max_size", 3),
            min_size=ng_config.get("min_size", 1),
            launch_template=aws.autoscaling.GroupLaunchTemplateArgs(
                id=lt.id,
                version=lt.latest_version.apply(str),
            ),
            instance_refresh=aws.autoscaling.GroupInstanceRefreshArgs(
                strategy="Rolling",
                preferences=aws.autoscaling.GroupInstanceRefreshPreferencesArgs(
                    min_healthy_percentage=ng_config.get("refresh_min_healthy_percent", 90),
                    skip_matching=ng_config.get("refresh_skip_matching", True),
                ),
            ),
            health_check_type="EC2",
            health_check_grace_period=300,
            termination_policies=["OldestInstance"],
            tags=[
                aws.autoscaling.GroupTagArgs(
                    key="Name",
                    value=f"{cluster_name}-{ng_name}-node",
                    propagate_at_launch=True,
                ),
                aws.autoscaling.GroupTagArgs(
                    key="NodeGroup",
                    value=ng_name,
                    propagate_at_launch=True,
                ),
                aws.autoscaling.GroupTagArgs(
                    key=f"kubernetes.io/cluster/{cluster_name}",
                    value="owned",
                    propagate_at_launch=True,
                ),
            ] + [
                aws.autoscaling.GroupTagArgs(
                    key=k,
                    value=v,
                    propagate_at_launch=True,
                )
                for k, v in tags.items()
            ],
            opts=pulumi.ResourceOptions(depends_on=[lt]),
        )

        #TODO: wait_for_capacity_timeout review if this paramter add automated wait 
        
        autoscaling_groups[ng_name] = asg

        readiness_timeout = ng_config.get("readiness_timeout_seconds", 600)
        readiness_poll_interval = ng_config.get("readiness_poll_interval_seconds", 15)

        if ng_config.get("await", True):
            asg_ready = WaitForAsgReady(
                f"{cluster_name}-{ng_name}-asg-ready",
                asg_name=asg.name,
                region=region,
                timeout_seconds=readiness_timeout,
                poll_interval_seconds=readiness_poll_interval,
                cluster_name=cluster_name,
                cluster_endpoint=cluster_endpoint,
                cluster_ca_data=cluster_ca_data,
                node_group_label=ng_name,
                opts=pulumi.ResourceOptions(depends_on=[asg]),
            )
            asg_readiness_checks[ng_name] = asg_ready
        else:
            pulumi.log.info(
                f"Skipping readiness wait for node group {ng_name} because await is not enabled."
            )
    
    return {
        "autoscaling_group_names": {k: v.name for k, v in autoscaling_groups.items()},
        "launch_template_ids": {k: v.id for k, v in launch_templates.items()},
        "autoscaling_group_readiness": asg_readiness_checks,
    }


def await_node_groups_ready(
    node_groups_config: Dict[str, Any],
    readiness_checks: Dict[str, WaitForAsgReady],
) -> pulumi.Output[bool]:
    """
    Wait for all node groups with await=True to report readiness.

    Args:
        node_groups_config: Node group configuration dictionary.
        readiness_checks: Mapping of node group names to readiness resources.

    Returns:
        A Pulumi Output that resolves to True once all awaited node groups are ready.
        Returns a resolved Output immediately when no waits are needed.
    """

    awaited_groups = [
        name for name, config in node_groups_config.items() if config.get("await", True)
    ]

    if not awaited_groups:
        pulumi.log.info("No node groups configured with await=True; skipping readiness wait.")
        return pulumi.Output.from_input(True)

    if pulumi.runtime.is_dry_run():
        pulumi.log.info("Preview: skipping node group readiness wait.")
        return pulumi.Output.from_input(True)

    missing_checks = [name for name in awaited_groups if name not in readiness_checks]
    if missing_checks:
        pulumi.log.warn(
            f"Missing readiness checks for node groups configured with await=True: {', '.join(missing_checks)}"
        )

    ready_outputs = [
        readiness_checks[name].ready for name in awaited_groups if name in readiness_checks
    ]

    if not ready_outputs:
        pulumi.log.info("No readiness checks available; skipping wait.")
        return pulumi.Output.from_input(True)

    pulumi.log.info("Waiting for node groups to become ready...")

    def _log_and_return(_: List[bool]) -> bool:
        pulumi.log.info("All awaited node groups report readiness.")
        return True

    return pulumi.Output.all(*ready_outputs).apply(_log_and_return)

