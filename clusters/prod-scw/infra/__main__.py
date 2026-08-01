"""
Entry point for the prod-scw Scaleway CAPS cluster stack.
"""

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLUSTER_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "iac-modules" / "cluster-infra" / "caps-v1"

CONFIG_PATH = CLUSTER_ROOT / "config.yaml"
CILIUM_VALUES_PATH = REPO_ROOT / "iac-modules" / "extensions" / "cilium" / "v1.18.3-v1" / "scaleway" / "release.yaml"
COREDNS_VALUES_PATH = REPO_ROOT / "iac-modules" / "extensions" / "coredns" / "v1.18.3-v1" / "release.yaml"
FLUX_VALUES_PATH = REPO_ROOT / "iac-modules" / "extensions" / "fluxcd" / "v2.17.1-v1" / "release.yaml"

if (module_root_str := str(MODULE_ROOT)) not in sys.path:
    sys.path.insert(0, module_root_str)

cluster_main = importlib.import_module("main").main

if __name__ == "__main__":
    cluster_main(
        config_path=CONFIG_PATH,
        cilium_values_path=CILIUM_VALUES_PATH,
        coredns_values_path=COREDNS_VALUES_PATH,
        flux_values_path=FLUX_VALUES_PATH,
    )
