from __future__ import annotations
from typing import Optional, Sequence
import pulumi
from .._inputs import AccessPermitRoleArgs


@pulumi.input_type
class AccessPermitArgs:
    def __init__(self, *,
                 parent_id: pulumi.Input[str],
                 subject_id: pulumi.Input[str],
                 roles: pulumi.Input[Sequence[pulumi.Input[AccessPermitRoleArgs]]]):
        pulumi.set(self, "parent_id", parent_id)
        pulumi.set(self, "subject_id", subject_id)
        pulumi.set(self, "roles", roles)


class AccessPermit(pulumi.CustomResource):
    """Grants IAM roles to a subject (service account, user, group) on a resource."""

    id: pulumi.Output[str]
    created_at: pulumi.Output[str]

    @staticmethod
    def get(resource_name: str, id: pulumi.Input[str],
            opts: Optional[pulumi.ResourceOptions] = None) -> "AccessPermit":
        opts = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(id=id))
        return AccessPermit(resource_name, opts=opts)

    def __init__(self,
                 resource_name: str,
                 args: Optional[AccessPermitArgs] = None,
                 opts: Optional[pulumi.ResourceOptions] = None,
                 *,
                 parent_id: Optional[pulumi.Input[str]] = None,
                 subject_id: Optional[pulumi.Input[str]] = None,
                 roles: Optional[pulumi.Input[Sequence[pulumi.Input[AccessPermitRoleArgs]]]] = None):
        if args is not None:
            parent_id = args.parent_id
            subject_id = args.subject_id
            roles = args.roles

        props = {
            "parent_id": parent_id,
            "subject_id": subject_id,
            "roles": roles,
            "created_at": None,
        }
        super().__init__("nebius:iam:AccessPermit", resource_name, props, opts)
