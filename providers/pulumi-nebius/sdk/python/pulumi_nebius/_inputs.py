from __future__ import annotations
from typing import Any, Mapping, Optional, Sequence
import pulumi


@pulumi.input_type
class ProviderServiceAccountArgs:
    def __init__(self, *,
                 account_id: Optional[pulumi.Input[str]] = None,
                 account_id_env: Optional[pulumi.Input[str]] = None,
                 public_key_id: Optional[pulumi.Input[str]] = None,
                 public_key_id_env: Optional[pulumi.Input[str]] = None,
                 private_key_file: Optional[pulumi.Input[str]] = None,
                 private_key_file_env: Optional[pulumi.Input[str]] = None,
                 credentials_file: Optional[pulumi.Input[str]] = None):
        if account_id is not None:
            pulumi.set(self, "account_id", account_id)
        if account_id_env is not None:
            pulumi.set(self, "account_id_env", account_id_env)
        if public_key_id is not None:
            pulumi.set(self, "public_key_id", public_key_id)
        if public_key_id_env is not None:
            pulumi.set(self, "public_key_id_env", public_key_id_env)
        if private_key_file is not None:
            pulumi.set(self, "private_key_file", private_key_file)
        if private_key_file_env is not None:
            pulumi.set(self, "private_key_file_env", private_key_file_env)
        if credentials_file is not None:
            pulumi.set(self, "credentials_file", credentials_file)


# ── MK8S ──────────────────────────────────────────────────────────────────────

@pulumi.input_type
class ClusterControlPlaneEndpointsPublicEndpointArgs:
    def __init__(self, *,
                 allowed_cidrs: Optional[pulumi.Input[Sequence[pulumi.Input[str]]]] = None):
        if allowed_cidrs is not None:
            pulumi.set(self, "allowed_cidrs", allowed_cidrs)


@pulumi.input_type
class ClusterControlPlaneEndpointsArgs:
    def __init__(self, *,
                 public_endpoint: Optional[pulumi.Input[ClusterControlPlaneEndpointsPublicEndpointArgs]] = None):
        if public_endpoint is not None:
            pulumi.set(self, "public_endpoint", public_endpoint)


@pulumi.input_type
class ClusterControlPlaneKarpenterArgs:
    """Enables Karpenter node pool management on this cluster."""
    def __init__(self):
        pass


@pulumi.input_type
class ClusterControlPlaneArgs:
    def __init__(self, *,
                 subnet_id: pulumi.Input[str],
                 version: Optional[pulumi.Input[str]] = None,
                 etcd_cluster_size: Optional[pulumi.Input[int]] = None,
                 endpoints: Optional[pulumi.Input[ClusterControlPlaneEndpointsArgs]] = None,
                 karpenter: Optional[pulumi.Input[ClusterControlPlaneKarpenterArgs]] = None):
        pulumi.set(self, "subnet_id", subnet_id)
        if version is not None:
            pulumi.set(self, "version", version)
        if etcd_cluster_size is not None:
            pulumi.set(self, "etcd_cluster_size", etcd_cluster_size)
        if endpoints is not None:
            pulumi.set(self, "endpoints", endpoints)
        if karpenter is not None:
            pulumi.set(self, "karpenter", karpenter)


@pulumi.input_type
class ClusterKubeNetworkArgs:
    def __init__(self, *,
                 service_cidrs: Optional[pulumi.Input[Sequence[pulumi.Input[str]]]] = None):
        if service_cidrs is not None:
            pulumi.set(self, "service_cidrs", service_cidrs)


@pulumi.input_type
class NodeGroupTemplateResourcesArgs:
    def __init__(self, *,
                 platform: pulumi.Input[str],
                 preset: Optional[pulumi.Input[str]] = None):
        pulumi.set(self, "platform", platform)
        if preset is not None:
            pulumi.set(self, "preset", preset)


@pulumi.input_type
class NodeGroupTemplateBootDiskArgs:
    def __init__(self, *,
                 type: Optional[pulumi.Input[str]] = None,
                 size_gibibytes: Optional[pulumi.Input[int]] = None):
        if type is not None:
            pulumi.set(self, "type", type)
        if size_gibibytes is not None:
            pulumi.set(self, "size_gibibytes", size_gibibytes)


@pulumi.input_type
class NodeGroupTemplateNetworkInterfaceArgs:
    def __init__(self, *,
                 subnet_id: pulumi.Input[str],
                 public_ip_address: Optional[pulumi.Input[Any]] = None,
                 security_groups: Optional[pulumi.Input[Sequence[pulumi.Input[str]]]] = None):
        pulumi.set(self, "subnet_id", subnet_id)
        if public_ip_address is not None:
            pulumi.set(self, "public_ip_address", public_ip_address)
        if security_groups is not None:
            pulumi.set(self, "security_groups", security_groups)


@pulumi.input_type
class NodeGroupTemplateTaintArgs:
    def __init__(self, *,
                 key: pulumi.Input[str],
                 value: pulumi.Input[str],
                 effect: pulumi.Input[str]):
        pulumi.set(self, "key", key)
        pulumi.set(self, "value", value)
        pulumi.set(self, "effect", effect)


@pulumi.input_type
class NodeGroupTemplateGpuSettingsArgs:
    def __init__(self, *, drivers_preset: pulumi.Input[str]):
        pulumi.set(self, "drivers_preset", drivers_preset)


@pulumi.input_type
class NodeGroupTemplateArgs:
    def __init__(self, *,
                 resources: pulumi.Input[NodeGroupTemplateResourcesArgs],
                 boot_disk: Optional[pulumi.Input[NodeGroupTemplateBootDiskArgs]] = None,
                 network_interfaces: Optional[pulumi.Input[Sequence[pulumi.Input[NodeGroupTemplateNetworkInterfaceArgs]]]] = None,
                 taints: Optional[pulumi.Input[Sequence[pulumi.Input[NodeGroupTemplateTaintArgs]]]] = None,
                 gpu_settings: Optional[pulumi.Input[NodeGroupTemplateGpuSettingsArgs]] = None,
                 cloud_init_user_data: Optional[pulumi.Input[str]] = None,
                 max_pods: Optional[pulumi.Input[int]] = None,
                 preemptible: Optional[pulumi.Input[Any]] = None):
        pulumi.set(self, "resources", resources)
        if boot_disk is not None:
            pulumi.set(self, "boot_disk", boot_disk)
        if network_interfaces is not None:
            pulumi.set(self, "network_interfaces", network_interfaces)
        if taints is not None:
            pulumi.set(self, "taints", taints)
        if gpu_settings is not None:
            pulumi.set(self, "gpu_settings", gpu_settings)
        if cloud_init_user_data is not None:
            pulumi.set(self, "cloud_init_user_data", cloud_init_user_data)
        if max_pods is not None:
            pulumi.set(self, "max_pods", max_pods)
        if preemptible is not None:
            pulumi.set(self, "preemptible", preemptible)


@pulumi.input_type
class NodeGroupAutoscalingArgs:
    def __init__(self, *,
                 min_node_count: pulumi.Input[int],
                 max_node_count: pulumi.Input[int]):
        pulumi.set(self, "min_node_count", min_node_count)
        pulumi.set(self, "max_node_count", max_node_count)


# ── VPC ───────────────────────────────────────────────────────────────────────

@pulumi.input_type
class SubnetIpv4Args:
    def __init__(self, *, cidr: pulumi.Input[str]):
        pulumi.set(self, "cidr", cidr)


# ── IAM ───────────────────────────────────────────────────────────────────────

@pulumi.input_type
class AccessPermitRoleArgs:
    def __init__(self, *,
                 id: pulumi.Input[str],
                 container_id: Optional[pulumi.Input[str]] = None):
        pulumi.set(self, "id", id)
        if container_id is not None:
            pulumi.set(self, "container_id", container_id)
