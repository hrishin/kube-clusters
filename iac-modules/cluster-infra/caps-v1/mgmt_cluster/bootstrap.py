"""
Bootstrap a local kind management cluster and initialize CAPS (Cluster API Provider Scaleway).

The management cluster runs the CAPI + CAPS controllers that reconcile ScalewayCluster /
ScalewayMachine objects into real Scaleway infrastructure.  A single kind cluster named
`caps-mgmt-<cluster_name>` is created (idempotent: skipped if it already exists) and
`clusterctl init --infrastructure scaleway` is run against it.

The stdout of the bootstrap command is the raw kubeconfig for the management cluster, which
Pulumi captures as an Output[str] and passes to the kubernetes provider.
"""

from __future__ import annotations

from typing import Any, Dict

import pulumi
from pulumi_command import local as cmd


_BOOTSTRAP_SCRIPT = """\
set -euo pipefail

KIND_CLUSTER="caps-mgmt-${CAPS_CLUSTER_NAME}"

# Create kind cluster (idempotent)
if ! kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
    echo "[caps] creating kind management cluster: ${KIND_CLUSTER}" >&2
    kind create cluster --name "${KIND_CLUSTER}" --wait 5m
else
    echo "[caps] kind cluster ${KIND_CLUSTER} already exists, skipping creation" >&2
fi

# Write kubeconfig to a temp file for clusterctl
KUBECONFIG_FILE="/tmp/${KIND_CLUSTER}.kubeconfig"
kind get kubeconfig --name "${KIND_CLUSTER}" > "${KUBECONFIG_FILE}"

# Run clusterctl init (idempotent -- providers that are already installed are skipped)
KUBECONFIG="${KUBECONFIG_FILE}" clusterctl init \
    --infrastructure scaleway \
    2>&1 | sed 's/^/[clusterctl] /' >&2 || \
    echo "[caps] clusterctl init returned non-zero (providers may already be installed)" >&2

# Wait for CAPS controllers to be ready
KUBECONFIG="${KUBECONFIG_FILE}" kubectl wait deployment \
    --namespace caps-system \
    --all \
    --for=condition=Available \
    --timeout=5m 2>&1 | sed 's/^/[caps-wait] /' >&2 || true

# Output kubeconfig on stdout so Pulumi can capture it
cat "${KUBECONFIG_FILE}"
"""

_DELETE_SCRIPT = """\
set -euo pipefail
KIND_CLUSTER="caps-mgmt-${CAPS_CLUSTER_NAME}"
echo "[caps] deleting kind management cluster: ${KIND_CLUSTER}" >&2
kind delete cluster --name "${KIND_CLUSTER}" || true
"""


def bootstrap_management_cluster(
    cluster_name: str,
    scw_access_key: pulumi.Input[str],
    scw_secret_key: pulumi.Input[str],
) -> Dict[str, Any]:
    """
    Create a kind management cluster, install CAPS, and return the management kubeconfig.

    Returns:
        dict with key ``kubeconfig`` (Output[str]).
    """
    bootstrap_cmd = cmd.Command(
        f"{cluster_name}-mgmt-bootstrap",
        create=_BOOTSTRAP_SCRIPT,
        delete=_DELETE_SCRIPT,
        environment={
            "CAPS_CLUSTER_NAME": cluster_name,
            "SCW_ACCESS_KEY": scw_access_key,
            "SCW_SECRET_KEY": scw_secret_key,
        },
        opts=pulumi.ResourceOptions(additional_secret_outputs=["stdout"]),
    )

    return {
        "kubeconfig": bootstrap_cmd.stdout,
        "bootstrap_cmd": bootstrap_cmd,
    }
