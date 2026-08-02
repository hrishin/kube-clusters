"""
Pulumi provider for Nebius AI Cloud.

Resources are namespaced by service:

    import pulumi_nebius as nebius

    # Managed Kubernetes
    nebius.mk8s.Cluster(...)
    nebius.mk8s.NodeGroup(...)

    # Networking
    nebius.vpc.Network(...)
    nebius.vpc.Subnet(...)
    nebius.vpc.SecurityGroup(...)

    # IAM
    nebius.iam.ServiceAccount(...)
    nebius.iam.AccessPermit(...)

    # Provider configuration
    nebius.Provider(...)
"""

from .provider import Provider
from . import mk8s
from . import vpc
from . import iam

# Re-export input types at package level for convenience
from ._inputs import (
    ProviderServiceAccountArgs,
    ClusterControlPlaneArgs,
    ClusterControlPlaneEndpointsArgs,
    ClusterControlPlaneEndpointsPublicEndpointArgs,
    ClusterControlPlaneKarpenterArgs,
    ClusterKubeNetworkArgs,
    NodeGroupTemplateArgs,
    NodeGroupTemplateResourcesArgs,
    NodeGroupTemplateBootDiskArgs,
    NodeGroupTemplateNetworkInterfaceArgs,
    NodeGroupTemplateTaintArgs,
    NodeGroupTemplateGpuSettingsArgs,
    NodeGroupAutoscalingArgs,
    SubnetIpv4Args,
    AccessPermitRoleArgs,
)

__version__ = "0.6.35"
__all__ = [
    "Provider",
    "ProviderServiceAccountArgs",
    "mk8s",
    "vpc",
    "iam",
    # input types
    "ClusterControlPlaneArgs",
    "ClusterControlPlaneEndpointsArgs",
    "ClusterControlPlaneEndpointsPublicEndpointArgs",
    "ClusterControlPlaneKarpenterArgs",
    "ClusterKubeNetworkArgs",
    "NodeGroupTemplateArgs",
    "NodeGroupTemplateResourcesArgs",
    "NodeGroupTemplateBootDiskArgs",
    "NodeGroupTemplateNetworkInterfaceArgs",
    "NodeGroupTemplateTaintArgs",
    "NodeGroupTemplateGpuSettingsArgs",
    "NodeGroupAutoscalingArgs",
    "SubnetIpv4Args",
    "AccessPermitRoleArgs",
]
