"""
Nebius MK8s cluster — prod entry point.
Mirrors clusters/prod/infra/__main__.py → delegates to the nebius-mk8s-v1 module.
"""

import sys
from pathlib import Path

import yaml
import pulumi
import pulumi_nebius as nebius

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

# ── Provider ────────────────────────────────────────────────────────────────

provider = nebius.Provider(
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
    provider=provider,
)
