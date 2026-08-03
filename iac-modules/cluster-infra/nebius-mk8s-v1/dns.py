"""
DNS zone management for Nebius clusters.

Phase 1 (first pulumi up): creates the Nebius public DNS zone + A record.
  → outputs dns_zone_id so you can discover the zone's nameservers via:
      nebius dns zone get --id <dns_zone_id>

Phase 2 (after nameservers are known): sets nebius_dns_nameservers in stack
  config and re-runs pulumi up to create Cloudflare NS delegation.
"""

import pulumi
import pulumi_nebius as nebius


def setup_dns(
    *,
    cluster_name: str,
    project_id: str,
    zone_domain: str,           # e.g. "nebius.kube.hrishi.dev."
    cluster_subdomain: str,     # e.g. "cluster1.eu-north-1"
    lb_ip: str,                 # e.g. "89.169.102.151"
    record_ttl: int = 300,
    provider: nebius.Provider,
) -> nebius.DnsV1Zone:
    """Create a public Nebius DNS zone and an A record for the cluster LB IP."""

    if not zone_domain.endswith("."):
        zone_domain += "."

    zone = nebius.DnsV1Zone(
        f"{cluster_name}-dns-zone",
        parent_id=project_id,
        name=f"{cluster_name}-dns",
        domain_name=zone_domain,
        opts=pulumi.ResourceOptions(provider=provider),
    )

    nebius.DnsV1Record(
        f"{cluster_name}-dns-cluster-a",
        parent_id=zone.id,
        relative_name=cluster_subdomain,
        type="A",
        data=lb_ip,
        ttl=float(record_ttl),
        opts=pulumi.ResourceOptions(provider=provider, depends_on=[zone]),
    )

    fqdn = f"{cluster_subdomain}.{zone_domain.rstrip('.')}"

    pulumi.export("dns_zone_id",      zone.id)
    pulumi.export("dns_zone_domain",  zone_domain)
    pulumi.export("dns_cluster_fqdn", fqdn)

    return zone
