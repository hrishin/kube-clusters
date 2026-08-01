"""
Scaleway management cluster — Pulumi entry point.

Bootstrap flow:
  1. kind cluster  +  clusterctl init (CAPS controllers)
  2. CAPI/CAPS provisions the management cluster on Scaleway
  3. Cilium + CoreDNS installed on the Scaleway cluster
  4. Flux bootstrapped; GitOps drives CCM, CSI, and other addons
  5. clusterctl init on the Scaleway cluster (CAPI controllers installed there)
  6. clusterctl move — CAPI state transferred from kind to Scaleway cluster
  7. (manual) tear down the kind bootstrap cluster
"""

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLUSTER_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "iac-modules" / "cluster-infra" / "caps-v1"

CONFIG_PATH = CLUSTER_ROOT / "config.yaml"
CILIUM_VALUES_PATH = REPO_ROOT / "iac-modules" / "extensions" / "cilium" / "caps-v1.18.3-v1" / "release.yaml"
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
        is_management_cluster=True,
    )
