from __future__ import annotations
from typing import Optional
import pulumi
from ._inputs import ProviderServiceAccountArgs


class Provider(pulumi.ProviderResource):
    """
    Pulumi provider for Nebius AI Cloud.

    Authentication (choose one):
    - Service account (recommended for automation): set service_account with
      account_id, public_key_id, private_key_file (or *_env variants).
    - IAM token: set token directly (12h lifetime, local dev only).
    """

    @staticmethod
    def get(resource_name: str, id: pulumi.Input[str],
            opts: Optional[pulumi.ResourceOptions] = None) -> "Provider":
        opts = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(id=id))
        return Provider(resource_name, opts=opts)

    def __init__(self,
                 resource_name: str,
                 opts: Optional[pulumi.ResourceOptions] = None,
                 service_account: Optional[pulumi.Input[ProviderServiceAccountArgs]] = None,
                 token: Optional[pulumi.Input[str]] = None,
                 domain: Optional[pulumi.Input[str]] = None,
                 parent_id: Optional[pulumi.Input[str]] = None,
                 module_name: Optional[pulumi.Input[str]] = None,
                 retries: Optional[pulumi.Input[int]] = None,
                 timeout: Optional[pulumi.Input[str]] = None):
        props = {
            "service_account": service_account,
            "token": token,
            "domain": domain,
            "parent_id": parent_id,
            "module_name": module_name,
            "retries": retries,
            "timeout": timeout,
        }
        super().__init__("nebius", resource_name, props, opts)
