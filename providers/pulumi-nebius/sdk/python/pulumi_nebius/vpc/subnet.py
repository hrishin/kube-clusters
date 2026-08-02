from __future__ import annotations
from typing import Mapping, Optional, Sequence
import pulumi
from .._inputs import SubnetIpv4Args


@pulumi.input_type
class SubnetArgs:
    def __init__(self, *,
                 parent_id: pulumi.Input[str],
                 network_id: pulumi.Input[str],
                 ipv4: pulumi.Input[SubnetIpv4Args],
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):
        pulumi.set(self, "parent_id", parent_id)
        pulumi.set(self, "network_id", network_id)
        pulumi.set(self, "ipv4", ipv4)
        if name is not None:
            pulumi.set(self, "name", name)
        if labels is not None:
            pulumi.set(self, "labels", labels)


class Subnet(pulumi.CustomResource):
    """Manages a Nebius VPC subnet."""

    id: pulumi.Output[str]
    name: pulumi.Output[str]
    parent_id: pulumi.Output[str]
    network_id: pulumi.Output[str]
    created_at: pulumi.Output[str]
    updated_at: pulumi.Output[str]

    @staticmethod
    def get(resource_name: str, id: pulumi.Input[str],
            opts: Optional[pulumi.ResourceOptions] = None) -> "Subnet":
        opts = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(id=id))
        return Subnet(resource_name, opts=opts)

    def __init__(self,
                 resource_name: str,
                 args: Optional[SubnetArgs] = None,
                 opts: Optional[pulumi.ResourceOptions] = None,
                 *,
                 parent_id: Optional[pulumi.Input[str]] = None,
                 network_id: Optional[pulumi.Input[str]] = None,
                 ipv4: Optional[pulumi.Input[SubnetIpv4Args]] = None,
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):
        if args is not None:
            parent_id = args.parent_id
            network_id = args.network_id
            ipv4 = args.ipv4
            name = args.name
            labels = args.labels

        props = {
            "parent_id": parent_id,
            "network_id": network_id,
            "ipv4": ipv4,
            "name": name,
            "labels": labels,
            "created_at": None,
            "updated_at": None,
        }
        super().__init__("nebius:vpc:Subnet", resource_name, props, opts)
