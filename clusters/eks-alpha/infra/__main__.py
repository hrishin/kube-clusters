"""
EKS Cluster Infrastructure with Pulumi
Converted from Terraform/Terragrunt setup

This is the main entry point that orchestrates all modules.
"""

import importlib
import sys
from pathlib import Path

import pulumi

REPO_ROOT = Path(__file__).resolve().parents[3]
CLUSTER_INFRA_DIR = Path(__file__).resolve().parent
CLUSTER_ROOT = CLUSTER_INFRA_DIR.parent

_cluster_version = pulumi.Config().get("cluster_version") or "1.36"
_infra_dir = REPO_ROOT / "iac-modules" / "cluster-infra"

# Pick the highest revision for the given k8s version: eks-v1.36-v1, eks-v1.36-v2 → eks-v1.36-v2
_candidates = sorted(
    (p for p in _infra_dir.iterdir() if p.is_dir() and p.name.startswith(f"eks-v{_cluster_version}-v")),
    key=lambda p: int(p.name.rsplit("-v", 1)[-1]),
)
if not _candidates:
    _available = [p.name for p in _infra_dir.iterdir() if p.is_dir() and p.name.startswith("eks-")]
    raise RuntimeError(
        f"No eks module for Kubernetes {_cluster_version}. "
        f"Available: {_available}"
    )
MODULE_ROOT = _candidates[-1]
NODE_GROUPS_CONFIG_PATH = CLUSTER_ROOT / "config.yaml"
CILIUM_VALUES_PATH = REPO_ROOT / "iac-modules" / "extensions" / "cilium" / "v1.18.3-v1" / "base" / "release.yaml"
COREDNS_VALUES_PATH = REPO_ROOT / "iac-modules" / "extensions" / "coredns" / "current" / "release.yaml"
FLUX_VALUES_PATH = REPO_ROOT / "iac-modules" / "extensions" / "fluxcd" / "current" / "release.yaml"

if (module_root_str := str(MODULE_ROOT)) not in sys.path:
    sys.path.insert(0, module_root_str)

cluster_main = importlib.import_module("main").main

if __name__ == "__main__":
    cluster_main(
        node_groups_config_path=NODE_GROUPS_CONFIG_PATH,
        cilium_values_path=CILIUM_VALUES_PATH,
        coredns_values_path=COREDNS_VALUES_PATH,
        flux_values_path=FLUX_VALUES_PATH,
    )

