"""
DNS management for Nebius clusters.

The Nebius Pulumi SDK (v0.6.35) does not expose the publicZone scope for
DnsV1Zone — only the vpc scope exists in the bridged TF provider. We work
around this by creating the zone via the Nebius REST API through a local
Command resource, then use the native DnsV1Record for the A record.
"""
import json
import pulumi
import pulumi_nebius as nebius
import pulumi_command as command

_DNS_API = "https://dns.api.eu-north1.nebius.cloud/dns/v1"

# Shell snippet: fetch a fresh IAM token at deploy time (not stored in state)
_GET_TOKEN = "TOKEN=$(nebius iam get-access-token)"


def _find_zone_cmd(project_id: str, zone_domain: str) -> str:
    """Shell expression that prints the zone ID if the domain already exists."""
    py = (
        "import json,sys; d=json.load(sys.stdin); "
        "[print(z['metadata']['id']) for z in d.get('items',[]) "
        f"if z.get('spec',{{}}).get('domainName')=='{zone_domain}']"
    )
    return (
        f'curl -sf -H "Authorization: Bearer $TOKEN" '
        f'"{_DNS_API}/zones?parentId={project_id}" | python3 -c "{py}" | head -1'
    )


def setup_dns(
    *,
    cluster_name: str,
    project_id: str,
    zone_domain: str,           # e.g. "nebius.kube.hrishi.dev."
    cluster_subdomain: str,     # e.g. "cluster1.eu-north-1"
    lb_ip: str,
    record_ttl: int = 300,
    provider: nebius.Provider,
) -> None:
    """Create a public Nebius DNS zone and A record for the cluster LB IP."""

    if not zone_domain.endswith("."):
        zone_domain += "."

    zone_body = json.dumps({
        "parentId": project_id,
        "name": f"{cluster_name}-dns",
        "spec": {
            "domainName": zone_domain,
            "scope": {"publicZone": {}},
        },
    })

    find_zone = _find_zone_cmd(project_id, zone_domain)

    # ── Zone (via REST API — SDK lacks publicZone scope) ──────────────────────

    zone_cmd = command.local.Command(
        f"{cluster_name}-dns-zone",
        create=f"""
set -eo pipefail
{_GET_TOKEN}
EXISTING=$({find_zone})
if [ -n "$EXISTING" ]; then
  echo "$EXISTING"
else
  curl -sf -X POST \\
    -H "Authorization: Bearer $TOKEN" \\
    -H "Content-Type: application/json" \\
    "{_DNS_API}/zones" \\
    -d '{zone_body}' | \\
    python3 -c "import json,sys; print(json.load(sys.stdin)['metadata']['id'])"
fi
""".strip(),
        delete=f"""
set -e
{_GET_TOKEN} 2>/dev/null || true
ZONE_ID=$({find_zone})
[ -z "$ZONE_ID" ] && exit 0
curl -sf -X DELETE \\
  -H "Authorization: Bearer $TOKEN" \\
  "{_DNS_API}/zones/$ZONE_ID" || true
""".strip(),
    )

    zone_id = zone_cmd.stdout.apply(str.strip)

    # ── A record (via SDK DnsV1Record — works fine with existing zone ID) ────

    nebius.DnsV1Record(
        f"{cluster_name}-dns-cluster-a",
        parent_id=zone_id,
        relative_name=cluster_subdomain,
        type="A",
        data=lb_ip,
        ttl=float(record_ttl),
        opts=pulumi.ResourceOptions(provider=provider, depends_on=[zone_cmd]),
    )

    fqdn = f"{cluster_subdomain}.{zone_domain.rstrip('.')}"
    pulumi.export("dns_zone_id",      zone_id)
    pulumi.export("dns_zone_domain",  zone_domain)
    pulumi.export("dns_cluster_fqdn", fqdn)
