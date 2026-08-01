"""
Orchestrates Scaleway CAPS cluster provisioning.

Sequence:
  1. Bootstrap a local kind management cluster and install CAPS providers.
  2. Apply CAPI / ScalewayCluster resources to the management cluster.
  3. Wait for the workload cluster to become Ready.
  4. Install Cilium CNI (required before nodes reach Ready).
  5. Install CoreDNS.
  6. Bootstrap Flux (which then drives GitOps for CCM, CSI, Cluster Autoscaler).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pulumi
import pulumi_kubernetes as k8s

from caps_cluster.cluster import create_caps_cluster, wait_for_workload_cluster
from kubernetes_addons.addons import bootstrap_flux, create_kubernetes_addons
from mgmt_cluster.bootstrap import bootstrap_management_cluster
from shared.config import load_cluster_config

REPO_ROOT = Path(__file__).resolve().parents[3]


def main(
    *,
    config_path: Path | str,
    cilium_values_path: Path | str,
    coredns_values_path: Path | str,
    flux_values_path: Path | str,
    is_management_cluster: bool = False,
) -> None:
    config_path = Path(config_path)
    cilium_values_path = Path(cilium_values_path)
    coredns_values_path = Path(coredns_values_path)
    flux_values_path = Path(flux_values_path)

    config = load_cluster_config(config_path)
    node_groups = config.pop("node_groups", {})

    flux_git_secret_values_path = config.get("flux_git_secret_values_path")
    if flux_git_secret_values_path:
        p = Path(flux_git_secret_values_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        config["flux_git_secret_values_path"] = str(p)
        flux_git_secret_values_path = config["flux_git_secret_values_path"]

    cluster_name: str = config["cluster_name"]

    # -----------------------------------------------------------------------
    # 1. Bootstrap management cluster (kind + clusterctl init --infrastructure scaleway)
    # -----------------------------------------------------------------------
    pulumi.log.info("Bootstrapping CAPS management cluster...")
    mgmt = bootstrap_management_cluster(
        cluster_name=cluster_name,
        scw_access_key=config["scw_access_key"],
        scw_secret_key=config["scw_secret_key"],
    )

    mgmt_k8s_provider = k8s.Provider(
        f"{cluster_name}-mgmt-k8s-provider",
        kubeconfig=mgmt["kubeconfig"],
        opts=pulumi.ResourceOptions(depends_on=[mgmt["bootstrap_cmd"]]),
    )

    # -----------------------------------------------------------------------
    # 2. Create CAPI / CAPS resources on the management cluster
    # -----------------------------------------------------------------------
    pulumi.log.info("Creating CAPI/CAPS cluster resources...")
    capi_resources = create_caps_cluster(
        cluster_name=cluster_name,
        kubernetes_version=config["kubernetes_version"],
        scw_project_id=config["scw_project_id"],
        scw_access_key=config["scw_access_key"],
        scw_secret_key=config["scw_secret_key"],
        scw_region=config["scw_region"],
        control_plane_machine_type=config["control_plane_machine_type"],
        control_plane_machine_image=config["control_plane_machine_image"],
        control_plane_count=config["control_plane_count"],
        pod_cidr=config["pod_cidr_range"],
        service_cidr=config["service_cidr_range"],
        private_network_cidr=config["private_network_cidr"],
        node_groups=node_groups,
        k8s_provider=mgmt_k8s_provider,
    )

    # Flatten all CAPI resources into a dependency list for the wait command
    capi_dep_list: List[pulumi.Resource] = [
        r
        for key, r in capi_resources.items()
        if key != "worker_machine_deployments" and isinstance(r, pulumi.Resource)
    ]
    capi_dep_list.extend(capi_resources.get("worker_machine_deployments", []))

    # -----------------------------------------------------------------------
    # 3. Wait for workload cluster to be Ready, then get its kubeconfig
    # -----------------------------------------------------------------------
    pulumi.log.info("Waiting for workload cluster to become Ready...")
    workload_info = wait_for_workload_cluster(
        cluster_name=cluster_name,
        mgmt_kubeconfig=mgmt["kubeconfig"],
        depends_on=capi_dep_list,
    )

    workload_k8s_provider = k8s.Provider(
        f"{cluster_name}-workload-k8s-provider",
        kubeconfig=workload_info["kubeconfig"],
        opts=pulumi.ResourceOptions(depends_on=[workload_info["wait_cmd"]]),
    )

    # -----------------------------------------------------------------------
    # 4 & 5. Install Cilium and CoreDNS
    # -----------------------------------------------------------------------
    pulumi.log.info("Installing Cilium and CoreDNS...")
    addons = create_kubernetes_addons(
        cluster_name=cluster_name,
        cluster_endpoint=workload_info["endpoint"],
        private_network_cidr=config["private_network_cidr"],
        pod_cidr=config["pod_cidr_range"],
        enable_cilium=config["enable_cilium"],
        enable_coredns=config["enable_coredns"],
        k8s_provider=workload_k8s_provider,
        cilium_values_path=str(cilium_values_path) if cilium_values_path.exists() else None,
        coredns_values_path=str(coredns_values_path) if coredns_values_path.exists() else None,
    )

    # -----------------------------------------------------------------------
    # 6. Bootstrap Flux
    # -----------------------------------------------------------------------
    flux_resources: Dict[str, Any] = {}
    if config["enable_flux"]:
        pulumi.log.info("Bootstrapping Flux...")
        addon_deps: List[pulumi.Resource] = []
        for key in ("cilium_release", "coredns_release"):
            dep = addons.get(key)
            if dep:
                addon_deps.append(dep)

        flux_resources = bootstrap_flux(
            cluster_name=cluster_name,
            cluster_endpoint=workload_info["endpoint"],
            mgmt_kubeconfig=mgmt["kubeconfig"],
            k8s_provider=workload_k8s_provider,
            enable_flux=config["enable_flux"],
            flux_values_path=str(flux_values_path) if flux_values_path.exists() else None,
            flux_git_url=config["flux_git_url"],
            flux_git_branch=config["flux_git_branch"],
            flux_git_path=config["flux_git_path"],
            flux_git_secret_name=config["flux_git_secret_name"],
            flux_git_secret_values_path=flux_git_secret_values_path,
            flux_sops_secret_name=config["flux_sops_secret_name"],
            flux_git_interval=config["flux_git_interval"],
            flux_kustomization_interval=config["flux_kustomization_interval"],
            additional_dependencies=addon_deps,
        )

    # -----------------------------------------------------------------------
    # Exports
    # -----------------------------------------------------------------------
    pulumi.export("cluster_name", cluster_name)
    pulumi.export("scw_region", config["scw_region"])
    pulumi.export(
        "workload_kubeconfig",
        pulumi.Output.secret(workload_info["kubeconfig"]),
    )
    pulumi.export("control_plane_endpoint", workload_info["endpoint"])
    if addons.get("cilium_release"):
        pulumi.export("cilium_release_name", addons["cilium_release"].name)
    if addons.get("coredns_release"):
        pulumi.export("coredns_release_name", addons["coredns_release"].name)
    if flux_resources.get("flux_release"):
        pulumi.export("flux_release_name", flux_resources["flux_release"].name)
