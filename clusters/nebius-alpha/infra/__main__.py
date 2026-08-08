"""
Nebius MK8s cluster — entry point.
Mirrors clusters/eks-alpha/infra/__main__.py → delegates to the nebius-mk8s-vX.Y-v1 module.
"""

import subprocess
import sys
from pathlib import Path

import yaml
import pulumi
import pulumi_nebius as nebius

REPO_ROOT    = Path(__file__).resolve().parents[3]
CLUSTER_ROOT = Path(__file__).resolve().parent.parent

# ── Config ──────────────────────────────────────────────────────────────────

with open(CLUSTER_ROOT / "config.yaml") as f:
    cfg = yaml.safe_load(f)

# Nebius service account credentials live in the SOPS-encrypted repo-wide
# config rather than Pulumi secure config, so they're readable/rotatable the
# same way as the other provider credentials in that file (github, scaleway, hf).
_sops_config = subprocess.run(
    ["sops", "-d", str(REPO_ROOT / "config" / "config.enc.yaml")],
    check=True, capture_output=True, text=True,
)
_secrets = yaml.safe_load(_sops_config.stdout)
_nebius_secrets = _secrets["nebius"]
_cf_api_token = (_secrets.get("cf") or {}).get("api-key")

# Pick the highest nebius-mk8s-vX.Y-vN module for the configured k8s version.
_k8s_version = cfg["kubernetes_version"]
_infra_dir = REPO_ROOT / "iac-modules" / "cluster-infra"
_candidates = sorted(
    (p for p in _infra_dir.iterdir() if p.is_dir() and p.name.startswith(f"nebius-mk8s-v{_k8s_version}-v")),
    key=lambda p: int(p.name.rsplit("-v", 1)[-1]),
)
if not _candidates:
    _available = [p.name for p in _infra_dir.iterdir() if p.is_dir() and p.name.startswith("nebius-mk8s-")]
    raise RuntimeError(
        f"No nebius-mk8s module for Kubernetes {_k8s_version}. Available: {_available}"
    )
MODULE_ROOT = _candidates[-1]

sys.path.insert(0, str(MODULE_ROOT))
from main import main  # noqa: E402

account_id       = _nebius_secrets["account_id"]
public_key_id    = _nebius_secrets["public_key_id"]
private_key_file = _nebius_secrets["private_key_file"]

stack_cfg = pulumi.Config()
flux_git_url               = stack_cfg.get("flux_git_url") or "https://github.com/hrishin/eks-infra"
flux_git_branch            = stack_cfg.get("flux_git_branch") or "main"
flux_git_path              = stack_cfg.get("flux_git_path") or "./clusters/nebius-alpha/extensions"
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

_dns_cfg = cfg.get("dns") or {}
dns_zone        = _dns_cfg.get("zone")
dns_record_name = _dns_cfg.get("name")

FLUX_VALUES_PATH = str(REPO_ROOT / "iac-modules" / "extensions" / "fluxcd" / "v2.17.1-v1" / "release.yaml")

# ── Provider ─────────────────────────────────────────────────────────────────

nebius_provider = nebius.Provider(
    "nebius",
    service_account=nebius.ProviderServiceAccountArgs(
        account_id=account_id,
        public_key_id=public_key_id,
        private_key_file=private_key_file,
    ),
)

# ── Cluster ─────────────────────────────────────────────────────────────────

main(
    cluster_name=cfg["cluster_name"],
    project_id=cfg["project_id"],
    kubernetes_version=cfg["kubernetes_version"],
    node_groups_config=cfg["node_groups"],
    gpu_clusters_config=cfg.get("gpu_clusters"),
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
    dns_zone=dns_zone,
    dns_record_name=dns_record_name,
    cf_api_token=_cf_api_token,
)
