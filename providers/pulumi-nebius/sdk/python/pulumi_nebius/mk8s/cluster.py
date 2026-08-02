from __future__ import annotations
from typing import Mapping, Optional, Sequence
import pulumi
import pulumi.runtime
from .._inputs import (
    ClusterControlPlaneArgs,
    ClusterKubeNetworkArgs,
)


@pulumi.input_type
class ClusterArgs:
    def __init__(self, *,
                 parent_id: pulumi.Input[str],
                 control_plane: pulumi.Input[ClusterControlPlaneArgs],
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None,
                 kube_network: Optional[pulumi.Input[ClusterKubeNetworkArgs]] = None):
        pulumi.set(self, "parent_id", parent_id)
        pulumi.set(self, "control_plane", control_plane)
        if name is not None:
            pulumi.set(self, "name", name)
        if labels is not None:
            pulumi.set(self, "labels", labels)
        if kube_network is not None:
            pulumi.set(self, "kube_network", kube_network)

    @property
    @pulumi.getter(name="parentId")
    def parent_id(self) -> pulumi.Input[str]:
        return pulumi.get(self, "parent_id")

    @property
    @pulumi.getter(name="controlPlane")
    def control_plane(self) -> pulumi.Input[ClusterControlPlaneArgs]:
        return pulumi.get(self, "control_plane")

    @property
    @pulumi.getter
    def name(self) -> Optional[pulumi.Input[str]]:
        return pulumi.get(self, "name")

    @property
    @pulumi.getter
    def labels(self) -> Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]]:
        return pulumi.get(self, "labels")

    @property
    @pulumi.getter(name="kubeNetwork")
    def kube_network(self) -> Optional[pulumi.Input[ClusterKubeNetworkArgs]]:
        return pulumi.get(self, "kube_network")


class Cluster(pulumi.CustomResource):
    """
    Manages a Nebius Managed Kubernetes (MK8S) cluster.

    Example:
    ```python
    import pulumi_nebius as nebius

    cluster = nebius.mk8s.Cluster("prod",
        parent_id=project_id,
        name="prod-cluster",
        control_plane=nebius.mk8s.ClusterControlPlaneArgs(
            subnet_id=subnet.id,
            version="1.31",
            etcd_cluster_size=3,
            endpoints=nebius.mk8s.ClusterControlPlaneEndpointsArgs(
                public_endpoint=nebius.mk8s.ClusterControlPlaneEndpointsPublicEndpointArgs(
                    allowed_cidrs=["0.0.0.0/0"],
                ),
            ),
        ),
        kube_network=nebius.mk8s.ClusterKubeNetworkArgs(
            service_cidrs=["10.96.0.0/16"],
        ),
    )
    ```
    """

    id: pulumi.Output[str]
    name: pulumi.Output[str]
    parent_id: pulumi.Output[str]
    created_at: pulumi.Output[str]
    updated_at: pulumi.Output[str]
    resource_version: pulumi.Output[int]
    status: pulumi.Output[dict]
    labels: pulumi.Output[Mapping[str, str]]

    @staticmethod
    def get(resource_name: str, id: pulumi.Input[str],
            opts: Optional[pulumi.ResourceOptions] = None) -> "Cluster":
        opts = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(id=id))
        return Cluster(resource_name, opts=opts)

    def __init__(self,
                 resource_name: str,
                 args: Optional[ClusterArgs] = None,
                 opts: Optional[pulumi.ResourceOptions] = None,
                 *,
                 parent_id: Optional[pulumi.Input[str]] = None,
                 control_plane: Optional[pulumi.Input[ClusterControlPlaneArgs]] = None,
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None,
                 kube_network: Optional[pulumi.Input[ClusterKubeNetworkArgs]] = None):

        if args is not None:
            parent_id = args.parent_id
            control_plane = args.control_plane
            name = args.name
            labels = args.labels
            kube_network = args.kube_network

        props = {
            "parent_id": parent_id,
            "control_plane": control_plane,
            "name": name,
            "labels": labels,
            "kube_network": kube_network,
            # computed outputs
            "created_at": None,
            "updated_at": None,
            "resource_version": None,
            "status": None,
        }
        super().__init__("nebius:mk8s:Cluster", resource_name, props, opts)
