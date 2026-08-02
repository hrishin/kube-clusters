from __future__ import annotations
from typing import Mapping, Optional
import pulumi


@pulumi.input_type
class SecurityGroupArgs:
    def __init__(self, *,
                 parent_id: pulumi.Input[str],
                 network_id: pulumi.Input[str],
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):
        pulumi.set(self, "parent_id", parent_id)
        pulumi.set(self, "network_id", network_id)
        if name is not None:
            pulumi.set(self, "name", name)
        if labels is not None:
            pulumi.set(self, "labels", labels)


class SecurityGroup(pulumi.CustomResource):
    """Manages a Nebius VPC security group."""

    id: pulumi.Output[str]
    name: pulumi.Output[str]
    created_at: pulumi.Output[str]

    @staticmethod
    def get(resource_name: str, id: pulumi.Input[str],
            opts: Optional[pulumi.ResourceOptions] = None) -> "SecurityGroup":
        opts = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(id=id))
        return SecurityGroup(resource_name, opts=opts)

    def __init__(self,
                 resource_name: str,
                 args: Optional[SecurityGroupArgs] = None,
                 opts: Optional[pulumi.ResourceOptions] = None,
                 *,
                 parent_id: Optional[pulumi.Input[str]] = None,
                 network_id: Optional[pulumi.Input[str]] = None,
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):
        if args is not None:
            parent_id = args.parent_id
            network_id = args.network_id
            name = args.name
            labels = args.labels

        props = {
            "parent_id": parent_id,
            "network_id": network_id,
            "name": name,
            "labels": labels,
            "created_at": None,
        }
        super().__init__("nebius:vpc:SecurityGroup", resource_name, props, opts)
