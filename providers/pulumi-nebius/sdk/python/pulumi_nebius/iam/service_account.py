from __future__ import annotations
from typing import Mapping, Optional
import pulumi


@pulumi.input_type
class ServiceAccountArgs:
    def __init__(self, *,
                 parent_id: pulumi.Input[str],
                 name: Optional[pulumi.Input[str]] = None,
                 labels: Optional[pulumi.Input[Mapping[str, pulumi.Input[str]]]] = None):
        pulumi.set(self, "parent_id", parent_id)
        if name is not None:
            pulumi.set(self, "name", name)
        if labels is not None:
            pulumi.set(self, "labels", labels)


class ServiceAccount(pulumi.CustomResource):
    """Manages a Nebius IAM service account."""

    id: pulumi.Output[str]
    name: pulumi.Output[str]
    parent_id: pulumi.Output[str]
    created_at: pulumi.Output[str]

    @staticmethod
    def get(resource_name: str, id: pulumi.Input[str],
            opts: Optional[pulumi.ResourceOptions] = None) -> "ServiceAccount":
        opts = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(id=id))
        return ServiceAccount(resource_name, opts=opts)

    def __init__(self,
                 resource_name: str,
                 args: Optional[ServiceAccountArgs] = None,
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
        }
        super().__init__("nebius:iam:ServiceAccount", resource_name, props, opts)
