"""
EKS Cluster Infrastructure with Pulumi
Converted from Terraform/Terragrunt setup

This is the main entry point that orchestrates all modules.
"""

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLUSTER_INFRA_DIR = Path(__file__).resolve().parent
CLUSTER_ROOT = CLUSTER_INFRA_DIR.parent
MODULE_ROOT = REPO_ROOT / "iac-modules" / "cluster-infra" / "v1.33-v1"
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

