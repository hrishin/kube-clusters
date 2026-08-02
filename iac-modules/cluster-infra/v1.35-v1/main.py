from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pulumi
import pulumi_aws as aws

# NOTE: eks_auth module kept but no longer called — auth is handled via
# EKS Access Entries (API mode) in create_eks_cluster().
# from eks_auth.auth import create_eks_auth
from eks_cluster.cluster import create_eks_cluster
from kubernetes_addons.addons import create_kubernetes_addons, bootstrap_flux
from networking.networking import create_networking
from node_groups.node_groups import create_node_groups, await_node_groups_ready
from iam.irsa import create_aws_lbc_irsa, create_karpenter_pod_identity
from shared.config import get_pulumi_config, load_node_groups_config

REPO_ROOT = Path(__file__).resolve().parents[3]


class NodeGroupReadinessBarrier(pulumi.ComponentResource):
    ready: pulumi.Output[bool]

    def __init__(
        self,
        name: str,
        ready_output: pulumi.Input[bool],
        opts: Optional[pulumi.ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:nodegroup:ReadinessBarrier", name, None, opts)

        self.ready = pulumi.Output.from_input(ready_output)

        self.register_outputs({"ready": self.ready})


def main(
    *,
    node_groups_config_path: Path | str,
    cilium_values_path: Path | str,
    coredns_values_path: Path | str,
    flux_values_path: Path | str,
) -> None:
    """
    Orchestrate the creation of the EKS cluster and supporting infrastructure.
    """

    node_groups_config_path = Path(node_groups_config_path)
    cilium_values_path = Path(cilium_values_path)
    coredns_values_path = Path(coredns_values_path)
    flux_values_path = Path(flux_values_path)

    # Load configuration
    config_data = get_pulumi_config()

    flux_git_secret_values_path = config_data.get("flux_git_secret_values_path")
    if flux_git_secret_values_path:
        flux_git_secret_values_path = Path(flux_git_secret_values_path)
        if not flux_git_secret_values_path.is_absolute():
            flux_git_secret_values_path = REPO_ROOT / flux_git_secret_values_path
        flux_git_secret_values_path = str(flux_git_secret_values_path)

    # Load node groups configuration from YAML
    node_groups_config = load_node_groups_config(str(node_groups_config_path))

    # Create tags
    tags = {
        "Environment": config_data["environment"],
        "ManagedBy": "pulumi",
        "Project": config_data["project"],
    }

    # 1. Create Networking Infrastructure
    pulumi.log.info("Creating networking infrastructure...")
    networking = create_networking(
        cluster_name=config_data["cluster_name"],
        vpc_cidr=config_data["vpc_cidr"],
        availability_zones=config_data["availability_zones"],
        tags=tags,
    )

    # 2. Create EKS Cluster
    pulumi.log.info("Creating EKS cluster...")
    cluster = create_eks_cluster(
        cluster_name=config_data["cluster_name"],
        cluster_version=config_data["cluster_version"],
        public_subnet_ids=networking["public_subnet_ids"],
        private_subnet_ids=networking["private_subnet_ids"],
        cluster_admin_user_arns=config_data["cluster_admin_user_arns"],
        tags=tags,
    )

    # Update the networking security group to allow traffic from cluster
    # This must be done after cluster is created
    def update_worker_sg(cluster_sg_id):
        if cluster_sg_id:
            return aws.ec2.SecurityGroupRule(
                "worker-from-cluster",
                type="ingress",
                from_port=0,
                to_port=0,
                protocol="-1",
                source_security_group_id=cluster_sg_id,
                security_group_id=networking["worker_node_security_group_id"],
                description="All traffic from EKS cluster",
                opts=pulumi.ResourceOptions(
                    depends_on=[cluster["cluster"]],
                ),
            )

    # Apply the security group rule
    pulumi.log.info("Allowing traffic from clster SG to wokers SG...")
    cluster["cluster_security_group_id"].apply(update_worker_sg)

    # 3. Build auth dependencies from EKS Access Entries (API-only mode)
    # The aws-auth ConfigMap is no longer needed — access is managed via
    # Access Entries created in create_eks_cluster().
    auth_dependencies = [
        *cluster["cluster_admin_access_policy_associations"],
        cluster["node_access_entry"],
    ]

    # 4. Install Kubernetes Add-ons (Cilium CNI and CoreDNS)
    pulumi.log.info("Installing Kubernetes add-ons...")
    addons = create_kubernetes_addons(
        cluster_name=config_data["cluster_name"],
        cluster_endpoint=cluster["cluster_endpoint"],
        cluster_ca_certificate=cluster["cluster_certificate_authority_data"],
        pod_cidr_range=config_data["pod_cidr_range"],
        enable_cilium=config_data["enable_cilium"],
        enable_coredns=config_data["enable_coredns"],
        cluster=cluster["cluster"],
        auth_dependencies=auth_dependencies,
        region=config_data["region"],
        cilium_values_path=str(cilium_values_path),
        coredns_values_path=str(coredns_values_path),
    )

    # 4.5. Create AWS Load Balancer Controller IRSA
    pulumi.log.info("Creating AWS Load Balancer Controller IRSA...")
    aws_lbc_irsa = create_aws_lbc_irsa(
        cluster_name=config_data["cluster_name"],
        oidc_issuer_url=cluster["cluster_oidc_issuer_url"],
        oidc_provider_arn=cluster["cluster_oidc_provider_arn"],
        tags=tags,
    )

    # 4.6. Create Karpenter Pod Identity
    pulumi.log.info("Creating Karpenter Pod Identity...")
    create_karpenter_pod_identity(
        cluster_name=config_data["cluster_name"],
        node_role_arn=cluster["node_group_service_role_arn"],
        pod_identity_agent_addon=cluster["pod_identity_agent_addon"],
        tags=tags,
    )

    # 5. Create Node Groups
    pulumi.log.info("Creating node groups...")
    node_groups = create_node_groups(
        cluster_name=config_data["cluster_name"],
        node_groups=node_groups_config,
        node_group_service_role_arn=cluster["node_group_service_role_arn"],
        node_instance_profile_name=cluster["node_instance_profile_name"],
        cluster_security_group_id=cluster["cluster_security_group_id"],
        worker_node_security_group_id=networking["worker_node_security_group_id"],
        private_subnet_ids=networking["private_subnet_ids"],
        cluster_endpoint=cluster["cluster_endpoint"],
        cluster_ca_data=cluster["cluster_certificate_authority_data"],
        tags=tags,
        region=config_data["region"],
        dns_cluster_ip=addons.get("coredns_cluster_ip"),
    )

    pulumi.log.info("Waiting for node groups to become ready...")
    ng_await = await_node_groups_ready(
        node_groups_config=node_groups_config,
        readiness_checks=node_groups["autoscaling_group_readiness"],
    )

    ng_readiness_barrier = NodeGroupReadinessBarrier(
        f"{config_data['cluster_name']}-node-group-readiness",
        ng_await,
    )

    flux_resources: Dict[str, Any] = {}
    if config_data["enable_flux"]:
        flux_dependencies: List[pulumi.Resource] = []
        for dependency_key in ("cilium_release", "coredns_release"):
            dependency = addons.get(dependency_key)
            if dependency:
                flux_dependencies.append(dependency)
        flux_dependencies.extend(node_groups["autoscaling_group_readiness"].values())
        flux_dependencies.append(ng_readiness_barrier)

        flux_resources = bootstrap_flux(
            cluster_name=config_data["cluster_name"],
            cluster_endpoint=cluster["cluster_endpoint"],
            cluster_ca_certificate=cluster["cluster_certificate_authority_data"],
            cluster=cluster["cluster"],
            auth_dependencies=auth_dependencies,
            region=config_data["region"],
            enable_flux=config_data["enable_flux"],
            flux_values_path=str(flux_values_path),
            flux_git_url=config_data["flux_git_url"],
            flux_git_branch=config_data["flux_git_branch"],
            flux_git_path=config_data["flux_git_path"],
            flux_git_secret_name=config_data["flux_git_secret_name"],
            flux_git_secret_values_path=flux_git_secret_values_path,
            flux_sops_secret_name=config_data["flux_sops_secret_name"],
            flux_git_interval=config_data["flux_git_interval"],
            flux_kustomization_interval=config_data["flux_kustomization_interval"],
            aws_lbc_role_arn=aws_lbc_irsa["role_arn"],
            nlb_dns=networking.get("nlb_dns_name"),
            additional_dependencies=flux_dependencies,
        )

    # Attach ASGs to NLB Target Groups
    for ng_name, asg_name in node_groups["autoscaling_group_names"].items():
        if "target_group_arn_80" in networking:
            aws.autoscaling.Attachment(
                f"{config_data['cluster_name']}-{ng_name}-asg-attach-80",
                autoscaling_group_name=asg_name,
                lb_target_group_arn=networking["target_group_arn_80"],
            )
        
        if "target_group_arn_443" in networking:
            aws.autoscaling.Attachment(
                f"{config_data['cluster_name']}-{ng_name}-asg-attach-443",
                autoscaling_group_name=asg_name,
                lb_target_group_arn=networking["target_group_arn_443"],
            )

    # Export outputs
    pulumi.export("cluster_name", cluster["cluster"].name)
    pulumi.export("cluster_endpoint", cluster["cluster_endpoint"])
    pulumi.export("cluster_security_group_id", cluster["cluster_security_group_id"])
    pulumi.export("cluster_arn", cluster["cluster"].arn)
    pulumi.export("cluster_oidc_issuer_url", cluster["cluster_oidc_issuer_url"])
    pulumi.export("cluster_oidc_provider_arn", cluster["cluster_oidc_provider_arn"])
    pulumi.export("node_group_service_role_arn", cluster["node_group_service_role_arn"])
    pulumi.export("node_instance_profile_name", cluster["node_instance_profile_name"])
    pulumi.export("vpc_id", networking["vpc_id"])
    pulumi.export("public_subnet_ids", networking["public_subnet_ids"])
    pulumi.export("private_subnet_ids", networking["private_subnet_ids"])
    pulumi.export("worker_node_security_group_id", networking["worker_node_security_group_id"])
    pulumi.export("autoscaling_group_names", node_groups["autoscaling_group_names"])
    pulumi.export("launch_template_ids", node_groups["launch_template_ids"])

    if "nlb_arn" in networking:
        pulumi.export("nlb_arn", networking["nlb_arn"])
        pulumi.export("nlb_dns_name", networking["nlb_dns_name"])
        pulumi.export("target_group_arn_80", networking["target_group_arn_80"])
        pulumi.export("target_group_arn_443", networking["target_group_arn_443"])
    # Export kubeconfig
    def create_kubeconfig(endpoint, ca_data, cluster_name):
        return pulumi.Output.all(endpoint, ca_data, cluster_name).apply(
            lambda args: f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {args[1]}
    server: {args[0]}
  name: {args[2]}
contexts:
- context:
    cluster: {args[2]}
    user: {args[2]}
  name: {args[2]}
current-context: {args[2]}
kind: Config
preferences: {{}}
users:
- name: {args[2]}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: aws
      args:
        - eks
        - get-token
        - --cluster-name
        - {args[2]}
        - --region
        - {config_data['region']}
"""
        )

    kubeconfig = create_kubeconfig(
        cluster["cluster_endpoint"],
        cluster["cluster_certificate_authority_data"],
        config_data["cluster_name"],
    )
    pulumi.export("kubeconfig", kubeconfig)

    # Export Helm release names
    if addons.get("cilium_release"):
        pulumi.export("cilium_release_name", addons["cilium_release"].name)
    if addons.get("coredns_release"):
        pulumi.export("coredns_release_name", addons["coredns_release"].name)
    if flux_resources.get("flux_release"):
        pulumi.export("flux_release_name", flux_resources["flux_release"].name)


