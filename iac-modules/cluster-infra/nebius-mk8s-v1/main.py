"""
Nebius MK8s cluster — orchestrator module.
Mirrors the structure of iac-modules/cluster-infra/v1.36-v1/main.py.
"""

from pathlib import Path
from typing import Any, Dict

import pulumi
import pulumi_nebius as nebius

from node_groups.node_groups import create_node_groups


def main(
    *,
    cluster_name: str,
    project_id: str,
    kubernetes_version: str,
    node_groups_config: Dict[str, Any],
    provider: nebius.Provider,
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

    # ── Exports ───────────────────────────────────────────────────────────────

    pulumi.export("cluster_id",   cluster.id)
    pulumi.export("cluster_name", cluster_name)
    pulumi.export("network_id",   network.id)
    pulumi.export("subnet_id",    subnet.id)
    pulumi.export("node_group_ids", {
        name: ng.id for name, ng in ng_result["node_groups"].items()
    })
