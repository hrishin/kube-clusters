"""
Nebius MK8s cluster — prod entry point.
Mirrors clusters/prod/infra/__main__.py → delegates to the nebius-mk8s-v1 module.
"""

import sys
from pathlib import Path
from typing import List

import yaml
import pulumi
import pulumi_nebius as nebius
import pulumi_cloudflare as cloudflare

REPO_ROOT    = Path(__file__).resolve().parents[3]
CLUSTER_ROOT = Path(__file__).resolve().parent.parent
MODULE_ROOT  = REPO_ROOT / "iac-modules" / "cluster-infra" / "nebius-mk8s-v1"

sys.path.insert(0, str(MODULE_ROOT))
from main import main  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────

with open(CLUSTER_ROOT / "config.yaml") as f:
    cfg = yaml.safe_load(f)

nebius_cfg       = pulumi.Config("nebius")
account_id       = nebius_cfg.require("account_id")
public_key_id    = nebius_cfg.require("public_key_id")
private_key_file = nebius_cfg.require("private_key_file")

stack_cfg = pulumi.Config()
flux_git_url               = stack_cfg.get("flux_git_url") or "https://github.com/hrishin/eks-infra"
flux_git_branch            = stack_cfg.get("flux_git_branch") or "main"
flux_git_path              = stack_cfg.get("flux_git_path") or "./clusters/nebius-prod/extensions"
flux_git_secret_name       = stack_cfg.get("flux_git_secret_name") or "flux-system"
_secret_path_raw = stack_cfg.get("flux_git_secret_values_path") or "config/config.enc.yaml"
# Resolve relative paths against repo root so they work regardless of cwd
flux_git_secret_values_path = (
    _secret_path_raw if Path(_secret_path_raw).is_absolute()
    else str(REPO_ROOT / _secret_path_raw)
)
flux_sops_secret_name      = stack_cfg.get("flux_sops_secret_name") or "sops-age"
flux_git_interval          = stack_cfg.get("flux_git_interval") or "1m0s"
flux_kustomization_interval = stack_cfg.get("flux_kustomization_interval") or "10m0s"

FLUX_VALUES_PATH = str(REPO_ROOT / "iac-modules" / "extensions" / "fluxcd" / "v2.17.1-v1" / "release.yaml")

# ── DNS config (from config.yaml dns: section) ──────────────────────────────

dns_cfg              = cfg.get("dns", {})
dns_zone_domain      = dns_cfg.get("zone_domain")        # "nebius.kube.hrishi.dev."
dns_cluster_subdomain = dns_cfg.get("cluster_subdomain") # "cluster1.eu-north-1"
dns_lb_ip            = dns_cfg.get("lb_ip")              # "89.169.102.151"

# Nebius zone nameservers — populated AFTER phase-1 pulumi up by querying:
#   nebius dns zone get --id $(pulumi stack output dns_zone_id)
# Then set in stack config:
#   pulumi config set --path nebius_dns_nameservers[0] "ns1.example."
#   pulumi config set --path nebius_dns_nameservers[1] "ns2.example."
nebius_dns_nameservers: List[str] = stack_cfg.get_object("nebius_dns_nameservers") or []

# ── Providers ────────────────────────────────────────────────────────────────

nebius_provider = nebius.Provider(
    "nebius",
    service_account=nebius.ProviderServiceAccountArgs(
        account_id=account_id,
        public_key_id=public_key_id,
        private_key_file=private_key_file,
    ),
)

cf_api_token = pulumi.Config("cloudflare").get_secret("apiToken")

# ── Cluster ─────────────────────────────────────────────────────────────────

main(
    cluster_name=cfg["cluster_name"],
    project_id=cfg["project_id"],
    kubernetes_version=cfg["kubernetes_version"],
    node_groups_config=cfg["node_groups"],
    provider=nebius_provider,
    flux_values_path=FLUX_VALUES_PATH,
    flux_git_url=flux_git_url,
    flux_git_branch=flux_git_branch,
    flux_git_path=flux_git_path,
    flux_git_secret_name=flux_git_secret_name,
    flux_git_secret_values_path=flux_git_secret_values_path,
    flux_sops_secret_name=flux_sops_secret_name,
    flux_git_interval=flux_git_interval,
    flux_kustomization_interval=flux_kustomization_interval,
    dns_zone_domain=dns_zone_domain,
    dns_cluster_subdomain=dns_cluster_subdomain,
    dns_lb_ip=dns_lb_ip,
)

# ── Cloudflare NS delegation (phase 2 — after Nebius NS servers are known) ──

if nebius_dns_nameservers and cf_api_token:
    cf_provider = cloudflare.Provider(
        "cloudflare",
        api_token=cf_api_token,
    )

    # Look up the hrishi.dev zone in Cloudflare
    cf_zone = cloudflare.get_zone_output(
        name="hrishi.dev",
        opts=pulumi.InvokeOptions(provider=cf_provider),
    )

    # nebius.kube.hrishi.dev → NS records pointing to Nebius zone nameservers
    for i, ns_server in enumerate(nebius_dns_nameservers):
        cloudflare.Record(
            f"nebius-kube-ns-{i}",
            zone_id=cf_zone.id,
            name="nebius.kube",
            type="NS",
            content=ns_server,
            ttl=300,
            opts=pulumi.ResourceOptions(provider=cf_provider),
        )

    pulumi.export("cloudflare_ns_records", nebius_dns_nameservers)
