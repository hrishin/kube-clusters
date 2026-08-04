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
    Provision Nebius VPC, MK8s cluster, and node groups.

    Args:
        cluster_name:        Cluster and resource name prefix.
        project_id:          Nebius IAM project/folder ID (parent for all resources).
        kubernetes_version:  Kubernetes version string (e.g. "1.35").
        node_groups_config:  Dict of node group name → config dict (from config.yaml).
        provider:            Configured nebius.Provider instance.
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

    # ── Node groups ───────────────────────────────────────────────────────────

    pulumi.log.info("Creating node groups...")
    ng_result = create_node_groups(
        cluster_name=cluster_name,
        cluster_id=cluster.id,
        subnet_id=subnet.id,
        node_groups=node_groups_config,
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
