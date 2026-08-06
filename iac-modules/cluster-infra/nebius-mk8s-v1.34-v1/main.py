"""
Nebius MK8s cluster — orchestrator module.
Mirrors the structure of iac-modules/cluster-infra/eks-v1.36-v1/main.py.
"""

from pathlib import Path
from typing import Any, Dict, Optional

import pulumi
import pulumi_nebius as nebius

from node_groups.node_groups import create_node_groups
from flux import bootstrap_flux


def main(
    *,
    cluster_name: str,
    project_id: str,
    kubernetes_version: str,
    node_groups_config: Dict[str, Any],
    gpu_clusters_config: Optional[Dict[str, Any]] = None,
    provider: nebius.Provider,
    flux_values_path: Optional[str] = None,
    flux_git_url: Optional[str] = None,
    flux_git_branch: str = "main",
    flux_git_path: Optional[str] = None,
    flux_git_secret_name: str = "flux-system",
    flux_git_secret_values_path: Optional[str] = None,
    flux_sops_secret_name: Optional[str] = None,
    flux_git_interval: str = "1m0s",
    flux_kustomization_interval: str = "10m0s",
) -> None:
    """
    Provision Nebius VPC, MK8s cluster, node groups, and optional GPU clusters
    (InfiniBand-fabric-based multi-node GPU networking).

    Args:
        cluster_name:          Cluster and resource name prefix.
        project_id:            Nebius IAM project/folder ID (parent for all resources).
        kubernetes_version:    Kubernetes version string (e.g. "1.35").
        node_groups_config:    Dict of node group name → config dict (from config.yaml).
        gpu_clusters_config:   Optional dict of gpu cluster name → {infiniband_fabric} config.
                                NOTE: this is InfiniBand-based multi-node GPU clustering
                                (ComputeV1GpuCluster), for platforms like gpu-h100-sxm.
                                It is unrelated to ComputeV1NvlInstanceGroup, which is a
                                different resource for cross-node NVLink Switch fabric —
                                that only exists for GB200/GB300 (Blackwell rack-scale)
                                platforms, not H100 SXM. H100 SXM NVLink is intra-node
                                only (8 GPUs/server); cross-node always goes over
                                InfiniBand, which is what this wires up.
        provider:              Configured nebius.Provider instance.
    """

    base_opts = pulumi.ResourceOptions(provider=provider)

    # ── VPC network ──────────────────────────────────────────────────────────

    pulumi.log.info("Creating VPC network...")
    network = nebius.VpcV1Network(
        f"{cluster_name}-network",
        parent_id=project_id,
        name=f"{cluster_name}-network",
        opts=base_opts,
    )

    # ── Subnet ───────────────────────────────────────────────────────────────

    pulumi.log.info("Creating subnet...")
    subnet = nebius.VpcV1Subnet(
        f"{cluster_name}-subnet",
        parent_id=project_id,
        network_id=network.id,
        name=f"{cluster_name}-subnet",
        # No explicit CIDR — Nebius allocates from the network's default private pools
        opts=pulumi.ResourceOptions(provider=provider, depends_on=[network]),
    )

    # ── MK8s cluster ─────────────────────────────────────────────────────────

    pulumi.log.info("Creating MK8s cluster...")
    cluster = nebius.Mk8sV1Cluster(
        cluster_name,
        parent_id=project_id,
        name=cluster_name,
        control_plane=nebius.Mk8sV1ClusterControlPlaneArgs(
            subnet_id=subnet.id,
            version=kubernetes_version,
            endpoints=nebius.Mk8sV1ClusterControlPlaneEndpointsArgs(
                public_endpoint=nebius.Mk8sV1ClusterControlPlaneEndpointsPublicEndpointArgs(),
            ),
            etcd_cluster_size=1,
        ),
        opts=pulumi.ResourceOptions(provider=provider, depends_on=[subnet]),
    )

    # ── GPU clusters (InfiniBand fabric) ────────────────────────────────────────
    # Must be created before node groups; node groups reference the cluster ID.

    gpu_cluster_ids: Dict[str, pulumi.Output] = {}
    if gpu_clusters_config:
        pulumi.log.info("Creating GPU clusters (InfiniBand fabric)...")
        for gc_name, gc_cfg in gpu_clusters_config.items():
            gpu_cluster = nebius.ComputeV1GpuCluster(
                f"{cluster_name}-{gc_name}",
                parent_id=project_id,
                name=f"{cluster_name}-{gc_name}",
                infiniband_fabric=gc_cfg["infiniband_fabric"],
                opts=base_opts,
            )
            gpu_cluster_ids[gc_name] = gpu_cluster.id
            pulumi.export(f"gpu_cluster_id_{gc_name}", gpu_cluster.id)

    # ── Node groups ───────────────────────────────────────────────────────────

    pulumi.log.info("Creating node groups...")
    ng_result = create_node_groups(
        cluster_name=cluster_name,
        cluster_id=cluster.id,
        subnet_id=subnet.id,
        node_groups=node_groups_config,
        gpu_cluster_ids=gpu_cluster_ids,
        provider=provider,
    )

    # ── Flux bootstrap ────────────────────────────────────────────────────────

    if flux_git_url:
        pulumi.log.info("Bootstrapping Flux...")
        bootstrap_flux(
            cluster_name=cluster_name,
            cluster=cluster,
            flux_values_path=flux_values_path,
            flux_git_url=flux_git_url,
            flux_git_branch=flux_git_branch,
            flux_git_path=flux_git_path or f"./clusters/{cluster_name}/extensions",
            flux_git_secret_name=flux_git_secret_name,
            flux_git_secret_values_path=flux_git_secret_values_path,
            flux_sops_secret_name=flux_sops_secret_name,
            flux_git_interval=flux_git_interval,
            flux_kustomization_interval=flux_kustomization_interval,
            additional_dependencies=list(ng_result["node_groups"].values()),
        )

    # ── Exports ───────────────────────────────────────────────────────────────

    pulumi.export("cluster_id",   cluster.id)
    pulumi.export("cluster_name", cluster_name)
    pulumi.export("network_id",   network.id)
    pulumi.export("subnet_id",    subnet.id)
    pulumi.export("node_group_ids", {
        name: ng.id for name, ng in ng_result["node_groups"].items()
    })
    pulumi.export(
        "cluster_endpoint",
        cluster.status.control_plane.endpoints.public_endpoint,
    )
