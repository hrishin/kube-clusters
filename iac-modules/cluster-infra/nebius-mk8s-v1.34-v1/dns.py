"""
Cloudflare DNS for the cluster's kgateway LoadBalancer.

Ports scripts/cf-dns.sh into the Pulumi resource graph: waits for Flux to
bootstrap and kgateway to provision the main-gateway LoadBalancer Service,
reads its external IP, then upserts a Cloudflare A record.

Runs scripts/cf_gateway_dns.py as a pulumi_command.local.Command rather than
a Pulumi dynamic provider. Dynamic providers are dill-pickled and re-imported
in a separate subprocess that only inherits PYTHONPATH from the shell that
launched `pulumi` — not this program's own sys.path.insert(), which is how
this cluster-infra module gets imported at all (it's selected dynamically
from clusters/<cluster>/infra/__main__.py based on config.yaml). That made
the class undiscoverable in the subprocess ("No module named 'dns'"). A
Command resource just execs a script by path, sidestepping that entirely.
"""

import json
import sys
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_command as command

_SCRIPT_PATH = Path(__file__).parent / "scripts" / "cf_gateway_dns.py"


def setup_gateway_dns(
    *,
    cluster_name: str,
    kubeconfig: pulumi.Input[str],
    cf_api_token: pulumi.Input[str],
    cf_zone_name: str,
    record_name: str,
    gateway_namespace: str = "kgateway-system",
    gateway_service_name: str = "main-gateway",
    ttl: int = 300,
    proxied: bool = False,
    poll_timeout_seconds: int = 900,
    poll_interval_seconds: int = 15,
    opts: Optional[pulumi.ResourceOptions] = None,
) -> command.local.Command:
    """Wait for the kgateway LoadBalancer IP and upsert it as a Cloudflare A record."""

    cmd = command.local.Command(
        f"{cluster_name}-cf-gateway-dns",
        create=f"{sys.executable} {_SCRIPT_PATH}",
        environment={
            "KUBECONFIG_CONTENT": pulumi.Output.secret(kubeconfig),
            "CF_API_TOKEN": pulumi.Output.secret(cf_api_token),
            "GATEWAY_NAMESPACE": gateway_namespace,
            "GATEWAY_SERVICE_NAME": gateway_service_name,
            "CF_ZONE_NAME": cf_zone_name,
            "RECORD_NAME": record_name,
            "TTL": str(ttl),
            "PROXIED": "true" if proxied else "false",
            "POLL_TIMEOUT_SECONDS": str(poll_timeout_seconds),
            "POLL_INTERVAL_SECONDS": str(poll_interval_seconds),
        },
        opts=opts,
    )

    result = cmd.stdout.apply(json.loads)
    pulumi.export("dns_gateway_ip", result["ip"])
    pulumi.export("dns_gateway_fqdn", result["fqdn"])

    return cmd
