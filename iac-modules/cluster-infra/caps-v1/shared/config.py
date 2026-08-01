"""
Configuration loading for Scaleway CAPS cluster provisioning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pulumi
import yaml


def load_cluster_config(config_path: Path | str) -> Dict[str, Any]:
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    # Secrets stay in Pulumi encrypted state, never in config.yaml.
    pulumi_cfg = pulumi.Config()

    return {
        "cluster_name": data["cluster_name"],
        "kubernetes_version": data.get("kubernetes_version", "v1.34.3"),
        "scw_region": data.get("scw_region", "fr-par"),
        "scw_project_id": pulumi_cfg.require_secret("scw_project_id"),
        "scw_access_key": pulumi_cfg.require_secret("scw_access_key"),
        "scw_secret_key": pulumi_cfg.require_secret("scw_secret_key"),
        "control_plane_machine_type": data.get("control_plane_machine_type", "DEV1-M"),
        "control_plane_machine_image": data.get("control_plane_machine_image", "cluster-api-ubuntu-2404-v1.34.3"),
        "control_plane_count": int(data.get("control_plane_count", 3)),
        "pod_cidr_range": data.get("pod_cidr_range", "192.168.0.0/16"),
        "service_cidr_range": data.get("service_cidr_range", "10.96.0.0/12"),
        "private_network_cidr": data.get("private_network_cidr", "172.16.0.0/22"),
        "enable_cilium": bool(data.get("enable_cilium", True)),
        "enable_coredns": bool(data.get("enable_coredns", True)),
        "enable_flux": bool(data.get("enable_flux", True)),
        "environment": data.get("environment", "production"),
        "project": data.get("project", "scw-cluster"),
        "flux_git_url": data.get("flux_git_url", "https://github.com/hrishin/kube-clusters"),
        "flux_git_branch": data.get("flux_git_branch", "main"),
        "flux_git_path": data.get("flux_git_path", "clusters/prod-scw/extensions"),
        "flux_git_secret_name": data.get("flux_git_secret_name", "flux-system"),
        "flux_git_secret_values_path": data.get("flux_git_secret_values_path", "config/config.enc.yaml"),
        "flux_sops_secret_name": data.get("flux_sops_secret_name", "sops-age"),
        "flux_git_interval": data.get("flux_git_interval", "1m0s"),
        "flux_kustomization_interval": data.get("flux_kustomization_interval", "10m0s"),
        "node_groups": data.get("node_groups", {}),
    }
