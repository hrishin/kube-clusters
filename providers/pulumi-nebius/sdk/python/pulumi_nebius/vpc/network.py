from __future__ import annotations
from typing import Mapping, Optional
import pulumi


@pulumi.input_type
class NetworkArgs:
    def __init__(self, *,
                 parent_id: pulumi.Input[str],
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):
        pulumi.set(self, "parent_id", parent_id)
        if name is not None:
            pulumi.set(self, "name", name)
        if labels is not None:
            pulumi.set(self, "labels", labels)


class Network(pulumi.CustomResource):
    """Manages a Nebius VPC network."""

    id: pulumi.Output[str]
    name: pulumi.Output[str]
    parent_id: pulumi.Output[str]
    created_at: pulumi.Output[str]
    updated_at: pulumi.Output[str]

    @staticmethod
    def get(resource_name: str, id: pulumi.Input[str],
            opts: Optional[pulumi.ResourceOptions] = None) -> "Network":
        opts = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(id=id))
        return Network(resource_name, opts=opts)

    def __init__(self,
                 resource_name: str,
                 args: Optional[NetworkArgs] = None,
                 opts: Optional[pulumi.ResourceOptions] = None,
                 *,
                 parent_id: Optional[pulumi.Input[str]] = None,
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):
        if args is not None:
            parent_id = args.parent_id
            name = args.name
            labels = args.labels

        props = {
            "parent_id": parent_id,
            "name": name,
            "labels": labels,
            "created_at": None,
            "updated_at": None,
        }
        super().__init__("nebius:vpc:Network", resource_name, props, opts)
