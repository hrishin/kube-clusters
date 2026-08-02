from __future__ import annotations
from typing import Mapping, Optional
import pulumi
from .._inputs import (
    NodeGroupAutoscalingArgs,
    NodeGroupTemplateArgs,
)


@pulumi.input_type
class NodeGroupArgs:
    def __init__(self, *,
                 parent_id: pulumi.Input[str],
                 template: pulumi.Input[NodeGroupTemplateArgs],
                 name: Optional[pulumi.Input[str]] = None,
                 version: Optional[pulumi.Input[str]] = None,
                 fixed_node_count: Optional[pulumi.Input[int]] = None,
                 autoscaling: Optional[pulumi.Input[NodeGroupAutoscalingArgs]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):
        pulumi.set(self, "parent_id", parent_id)
        pulumi.set(self, "template", template)
        if name is not None:
            pulumi.set(self, "name", name)
        if version is not None:
            pulumi.set(self, "version", version)
        if fixed_node_count is not None:
            pulumi.set(self, "fixed_node_count", fixed_node_count)
        if autoscaling is not None:
            pulumi.set(self, "autoscaling", autoscaling)
        if labels is not None:
            pulumi.set(self, "labels", labels)


class NodeGroup(pulumi.CustomResource):
    """
    Manages a Nebius MK8S node group (worker pool).

    Example:
    ```python
    import pulumi_nebius as nebius

    nodes = nebius.mk8s.NodeGroup("gpu-nodes",
        parent_id=cluster.id,
        name="gpu-nodes",
        version="1.31",
        fixed_node_count=2,
        template=nebius.mk8s.NodeGroupTemplateArgs(
            resources=nebius.mk8s.NodeGroupTemplateResourcesArgs(
                platform="gpu-h100-sxm",
                preset="1gpu-16vcpu-200gb",
            ),
            boot_disk=nebius.mk8s.NodeGroupTemplateBootDiskArgs(
                type="NETWORK_SSD",
                size_gibibytes=200,
            ),
            network_interfaces=[
                nebius.mk8s.NodeGroupTemplateNetworkInterfaceArgs(
                    subnet_id=subnet.id,
                    public_ip_address={},
                )
            ],
            gpu_settings=nebius.mk8s.NodeGroupTemplateGpuSettingsArgs(
                drivers_preset="cuda12",
            ),
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

    @staticmethod
    def get(resource_name: str, id: pulumi.Input[str],
            opts: Optional[pulumi.ResourceOptions] = None) -> "NodeGroup":
        opts = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(id=id))
        return NodeGroup(resource_name, opts=opts)

    def __init__(self,
                 resource_name: str,
                 args: Optional[NodeGroupArgs] = None,
                 opts: Optional[pulumi.ResourceOptions] = None,
                 *,
                 parent_id: Optional[pulumi.Input[str]] = None,
                 template: Optional[pulumi.Input[NodeGroupTemplateArgs]] = None,
                 name: Optional[pulumi.Input[str]] = None,
                 version: Optional[pulumi.Input[str]] = None,
                 fixed_node_count: Optional[pulumi.Input[int]] = None,
                 autoscaling: Optional[pulumi.Input[NodeGroupAutoscalingArgs]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):

        if args is not None:
            parent_id = args.parent_id
            template = args.template
            name = args.name
            version = args.version
            fixed_node_count = args.fixed_node_count
            autoscaling = args.autoscaling
            labels = args.labels

        props = {
            "parent_id": parent_id,
            "template": template,
            "name": name,
            "version": version,
            "fixed_node_count": fixed_node_count,
            "autoscaling": autoscaling,
            "labels": labels,
            "created_at": None,
            "updated_at": None,
            "resource_version": None,
            "status": None,
        }
        super().__init__("nebius:mk8s:NodeGroup", resource_name, props, opts)
