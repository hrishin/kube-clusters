"""
CAPI / CAPS workload cluster resources applied to the management cluster.

Creates:
  - Secret           -- Scaleway credentials consumed by the CAPS controller
  - ScalewayCluster  -- Scaleway infrastructure spec (private network, LB, gateway)
  - Cluster          -- CAPI Cluster tying infrastructure to the control plane
  - ScalewayMachineTemplate (control plane)
  - KubeadmControlPlane
  - ScalewayMachineTemplate (per node group)
  - KubeadmConfigTemplate   (per node group)
  - MachineDeployment       (per node group, with Cluster Autoscaler annotations)

After these resources are applied the CAPS controllers will provision the actual
Scaleway infrastructure.  Use wait_for_workload_cluster() to block until the
workload cluster endpoint is reachable.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pulumi
import pulumi_kubernetes as k8s


# ---------------------------------------------------------------------------
# CAPI / CAPS API versions
# ---------------------------------------------------------------------------
_CLUSTER_API = "cluster.x-k8s.io/v1beta1"
_CAPS_API = "infrastructure.cluster.x-k8s.io/v1alpha2"
_KUBEADM_BOOTSTRAP_API = "bootstrap.cluster.x-k8s.io/v1beta1"
_KUBEADM_CP_API = "controlplane.cluster.x-k8s.io/v1beta1"


def create_caps_cluster(
    cluster_name: str,
    kubernetes_version: str,
    scw_project_id: pulumi.Input[str],
    scw_access_key: pulumi.Input[str],
    scw_secret_key: pulumi.Input[str],
    scw_region: str,
    control_plane_machine_type: str,
    control_plane_machine_image: str,
    control_plane_count: int,
    pod_cidr: str,
    service_cidr: str,
    private_network_cidr: str,
    node_groups: Dict[str, Any],
    k8s_provider: k8s.Provider,
) -> Dict[str, Any]:
    """
    Apply all CAPI/CAPS manifests to the management cluster.

    Returns a dict of all created resources keyed by logical name.
    """
    resources: Dict[str, Any] = {}
    common_opts = pulumi.ResourceOptions(provider=k8s_provider)

    # ------------------------------------------------------------------
    # Scaleway credentials secret (consumed by CAPS controller)
    # ------------------------------------------------------------------
    creds_secret = k8s.core.v1.Secret(
        f"{cluster_name}-scw-credentials",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"{cluster_name}-credentials",
            namespace="default",
        ),
        string_data={
            "SCW_ACCESS_KEY": scw_access_key,
            "SCW_SECRET_KEY": scw_secret_key,
        },
        type="Opaque",
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            additional_secret_outputs=["stringData"],
        ),
    )
    resources["credentials_secret"] = creds_secret

    # ------------------------------------------------------------------
    # ScalewayCluster -- infrastructure spec
    # ------------------------------------------------------------------
    scw_cluster = k8s.apiextensions.CustomResource(
        f"{cluster_name}-scw-cluster",
        api_version=_CAPS_API,
        kind="ScalewayCluster",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=cluster_name,
            namespace="default",
        ),
        spec={
            "projectID": scw_project_id,
            "region": scw_region,
            "scalewaySecretName": creds_secret.metadata["name"],
            # Spread control-plane nodes across two zones for HA
            "failureDomains": [
                f"{scw_region}-1",
                f"{scw_region}-2",
            ],
            "network": {
                "privateNetwork": {
                    "enabled": True,
                    "subnet": private_network_cidr,
                },
                # Public gateway gives nodes outbound internet without public IPs
                "publicGateways": [
                    {"type": "VPC-GW-S", "zone": f"{scw_region}-1"},
                    {"type": "VPC-GW-S", "zone": f"{scw_region}-2"},
                ],
                "controlPlaneLoadBalancer": {
                    "type": "LB-S",
                },
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[creds_secret],
        ),
    )
    resources["scw_cluster"] = scw_cluster

    # ------------------------------------------------------------------
    # Control-plane ScalewayMachineTemplate
    # ------------------------------------------------------------------
    cp_machine_template = k8s.apiextensions.CustomResource(
        f"{cluster_name}-cp-machine-template",
        api_version=_CAPS_API,
        kind="ScalewayMachineTemplate",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"{cluster_name}-control-plane",
            namespace="default",
        ),
        spec={
            "template": {
                "spec": {
                    "commercialType": control_plane_machine_type,
                    "image": {"name": control_plane_machine_image},
                    "rootVolume": {"size": 20, "type": "block"},
                    # Nodes live on the private network; no public IPv4 needed
                    "publicNetwork": {"enableIPv4": False, "enableIPv6": False},
                }
            }
        },
        opts=common_opts,
    )
    resources["cp_machine_template"] = cp_machine_template

    # ------------------------------------------------------------------
    # KubeadmControlPlane
    # ------------------------------------------------------------------
    # cloud-provider=external is required when Scaleway CCM manages nodes/LBs
    _external_cloud_provider_args = {"cloud-provider": "external"}

    kubeadm_cp = k8s.apiextensions.CustomResource(
        f"{cluster_name}-kubeadm-cp",
        api_version=_KUBEADM_CP_API,
        kind="KubeadmControlPlane",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"{cluster_name}-control-plane",
            namespace="default",
        ),
        spec={
            "replicas": control_plane_count,
            "version": kubernetes_version,
            "machineTemplate": {
                "infrastructureRef": {
                    "apiVersion": _CAPS_API,
                    "kind": "ScalewayMachineTemplate",
                    "name": f"{cluster_name}-control-plane",
                    "namespace": "default",
                }
            },
            "kubeadmConfigSpec": {
                "clusterConfiguration": {
                    "apiServer": {
                        "extraArgs": _external_cloud_provider_args,
                    },
                    "controllerManager": {
                        "extraArgs": _external_cloud_provider_args,
                    },
                },
                "initConfiguration": {
                    "nodeRegistration": {
                        "kubeletExtraArgs": _external_cloud_provider_args,
                        # Scaleway private-network interface name on Ubuntu 24.04 is ens5+
                        # Use the node's private IP for kubelet registration
                        "criSocket": "unix:///run/containerd/containerd.sock",
                    }
                },
                "joinConfiguration": {
                    "nodeRegistration": {
                        "kubeletExtraArgs": _external_cloud_provider_args,
                        "criSocket": "unix:///run/containerd/containerd.sock",
                    }
                },
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[cp_machine_template],
        ),
    )
    resources["kubeadm_cp"] = kubeadm_cp

    # ------------------------------------------------------------------
    # CAPI Cluster (ties infrastructure + control-plane together)
    # ------------------------------------------------------------------
    cluster = k8s.apiextensions.CustomResource(
        f"{cluster_name}-capi-cluster",
        api_version=_CLUSTER_API,
        kind="Cluster",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=cluster_name,
            namespace="default",
        ),
        spec={
            "clusterNetwork": {
                "pods": {"cidrBlocks": [pod_cidr]},
                "services": {"cidrBlocks": [service_cidr]},
            },
            "infrastructureRef": {
                "apiVersion": _CAPS_API,
                "kind": "ScalewayCluster",
                "name": cluster_name,
                "namespace": "default",
            },
            "controlPlaneRef": {
                "apiVersion": _KUBEADM_CP_API,
                "kind": "KubeadmControlPlane",
                "name": f"{cluster_name}-control-plane",
                "namespace": "default",
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[scw_cluster, kubeadm_cp],
        ),
    )
    resources["cluster"] = cluster

    # ------------------------------------------------------------------
    # Per-node-group worker MachineDeployments
    # ------------------------------------------------------------------
    worker_resources: List[k8s.apiextensions.CustomResource] = []

    for ng_name, ng_config in node_groups.items():
        ng_resources = _create_machine_deployment(
            cluster_name=cluster_name,
            ng_name=ng_name,
            ng_config=ng_config,
            kubernetes_version=kubernetes_version,
            k8s_provider=k8s_provider,
            depends_on=[cluster],
        )
        resources[f"ng_{ng_name}_machine_template"] = ng_resources["machine_template"]
        resources[f"ng_{ng_name}_kubeadm_config"] = ng_resources["kubeadm_config"]
        resources[f"ng_{ng_name}_machine_deployment"] = ng_resources["machine_deployment"]
        worker_resources.append(ng_resources["machine_deployment"])

    resources["worker_machine_deployments"] = worker_resources
    return resources


def _create_machine_deployment(
    cluster_name: str,
    ng_name: str,
    ng_config: Dict[str, Any],
    kubernetes_version: str,
    k8s_provider: k8s.Provider,
    depends_on: List[Any],
) -> Dict[str, Any]:
    """Create a MachineDeployment with its templates for one node group."""
    resource_prefix = f"{cluster_name}-{ng_name}"

    commercial_type = ng_config.get("commercial_type", "DEV1-M")
    machine_image = ng_config.get("machine_image", "cluster-api-ubuntu-2404-v1.34.3")
    desired_size = ng_config.get("desired_size", 1)
    min_size = ng_config.get("min_size", 1)
    max_size = ng_config.get("max_size", 3)
    disk_size = ng_config.get("disk_size", 20)
    labels = ng_config.get("labels", {})
    taints = ng_config.get("taints", [])

    machine_template = k8s.apiextensions.CustomResource(
        f"{resource_prefix}-machine-template",
        api_version="infrastructure.cluster.x-k8s.io/v1alpha2",
        kind="ScalewayMachineTemplate",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=resource_prefix,
            namespace="default",
        ),
        spec={
            "template": {
                "spec": {
                    "commercialType": commercial_type,
                    "image": {"name": machine_image},
                    "rootVolume": {"size": disk_size, "type": "block"},
                    "publicNetwork": {"enableIPv4": False, "enableIPv6": False},
                }
            }
        },
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=depends_on),
    )

    # Convert taints list to kubeadm format
    kubeadm_taints = [
        {
            "key": t["key"],
            "value": t.get("value", ""),
            "effect": t.get("effect", "NoSchedule"),
        }
        for t in taints
    ]

    kubeadm_config = k8s.apiextensions.CustomResource(
        f"{resource_prefix}-kubeadm-config",
        api_version="bootstrap.cluster.x-k8s.io/v1beta1",
        kind="KubeadmConfigTemplate",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=resource_prefix,
            namespace="default",
        ),
        spec={
            "template": {
                "spec": {
                    "joinConfiguration": {
                        "nodeRegistration": {
                            "kubeletExtraArgs": {"cloud-provider": "external"},
                            "criSocket": "unix:///run/containerd/containerd.sock",
                            "taints": kubeadm_taints,
                        }
                    }
                }
            }
        },
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=depends_on),
    )

    # Cluster Autoscaler discovers MachineDeployments via these annotations
    md_annotations = {
        "cluster.x-k8s.io/cluster-name": cluster_name,
        "capacity.cluster-autoscaler.kubernetes.io/minSize": str(min_size),
        "capacity.cluster-autoscaler.kubernetes.io/maxSize": str(max_size),
    }

    machine_deployment = k8s.apiextensions.CustomResource(
        f"{resource_prefix}-machine-deployment",
        api_version="cluster.x-k8s.io/v1beta1",
        kind="MachineDeployment",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=resource_prefix,
            namespace="default",
            annotations=md_annotations,
        ),
        spec={
            "clusterName": cluster_name,
            "replicas": desired_size,
            "selector": {"matchLabels": {}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "clusterName": cluster_name,
                    "version": kubernetes_version,
                    "bootstrap": {
                        "configRef": {
                            "apiVersion": "bootstrap.cluster.x-k8s.io/v1beta1",
                            "kind": "KubeadmConfigTemplate",
                            "name": resource_prefix,
                            "namespace": "default",
                        }
                    },
                    "infrastructureRef": {
                        "apiVersion": "infrastructure.cluster.x-k8s.io/v1alpha2",
                        "kind": "ScalewayMachineTemplate",
                        "name": resource_prefix,
                        "namespace": "default",
                    },
                },
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[*depends_on, machine_template, kubeadm_config],
        ),
    )

    return {
        "machine_template": machine_template,
        "kubeadm_config": kubeadm_config,
        "machine_deployment": machine_deployment,
    }


def wait_for_workload_cluster(
    cluster_name: str,
    mgmt_kubeconfig: pulumi.Input[str],
    depends_on: List[Any],
) -> Dict[str, Any]:
    """
    Wait for the CAPI Cluster to become Ready, then retrieve the workload
    cluster kubeconfig via clusterctl.

    The stdout of this command is the workload cluster kubeconfig (raw YAML).
    The control-plane endpoint host is extracted and exported separately.
    """
    from pulumi_command import local as cmd

    _WAIT_SCRIPT = """\
set -euo pipefail

# Write management cluster kubeconfig from environment variable
MGMT_KUBECONFIG_FILE="/tmp/caps-mgmt-${CAPS_CLUSTER_NAME}.kubeconfig"
printf '%s' "${MGMT_KUBECONFIG}" > "${MGMT_KUBECONFIG_FILE}"

echo "[caps] waiting for CAPI Cluster to become Ready (timeout: 30m)..." >&2
KUBECONFIG="${MGMT_KUBECONFIG_FILE}" kubectl wait cluster "${CAPS_CLUSTER_NAME}" \\
    --namespace default \\
    --for=condition=Ready \\
    --timeout=30m >&2

echo "[caps] cluster is Ready, fetching workload kubeconfig..." >&2
# clusterctl get kubeconfig outputs the raw kubeconfig to stdout
KUBECONFIG="${MGMT_KUBECONFIG_FILE}" clusterctl get kubeconfig "${CAPS_CLUSTER_NAME}"
"""

    wait_cmd = cmd.Command(
        f"{cluster_name}-wait-workload-ready",
        create=_WAIT_SCRIPT,
        environment={
            "CAPS_CLUSTER_NAME": cluster_name,
            "MGMT_KUBECONFIG": mgmt_kubeconfig,
        },
        opts=pulumi.ResourceOptions(
            depends_on=depends_on,
            additional_secret_outputs=["stdout"],
        ),
    )

    # Extract the control-plane endpoint from the workload kubeconfig.
    # The kubeconfig server field looks like: https://<IP-or-DNS>:6443
    workload_kubeconfig = wait_cmd.stdout
    control_plane_endpoint = workload_kubeconfig.apply(
        lambda kc: _extract_server_host(kc)
    )

    return {
        "kubeconfig": workload_kubeconfig,
        "endpoint": control_plane_endpoint,
        "wait_cmd": wait_cmd,
    }


def _extract_server_host(kubeconfig_yaml: str) -> str:
    """Extract the hostname (without https:// and port) from a kubeconfig."""
    import yaml as _yaml

    try:
        kc = _yaml.safe_load(kubeconfig_yaml)
        server: str = kc["clusters"][0]["cluster"]["server"]
        # Strip scheme and port: https://1.2.3.4:6443 -> 1.2.3.4
        host = server.replace("https://", "").replace("http://", "").split(":")[0]
        return host
    except Exception:
        return ""
