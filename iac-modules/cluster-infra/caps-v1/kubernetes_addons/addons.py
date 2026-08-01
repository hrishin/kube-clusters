"""
Kubernetes add-ons for CAPS-provisioned workload clusters.

Installs Cilium, CoreDNS, and Flux via Helm.  The CNI must be installed before
nodes can reach Ready state; Cilium is therefore installed first and CoreDNS +
Flux depend on it.

Key differences from the EKS add-ons module:
- kubeconfig is a plain static file (no aws eks get-token exec plugin)
- k8sServicePort is 6443 (kubeadm default) not 443
- routingMode is native (Scaleway private-network is L2)
- A management-cluster kubeconfig Secret is created for Cluster Autoscaler
"""

from __future__ import annotations

import os
import subprocess
from copy import deepcopy
from typing import Any, Dict, List, Optional

import pulumi
import pulumi_kubernetes as k8s
import yaml


# ---------------------------------------------------------------------------
# Default Helm values
# ---------------------------------------------------------------------------

_DEFAULT_CILIUM_VALUES: Dict[str, Any] = {
    "autoDirectNodeRoutes": True,
    "bpf": {
        # veth is broadly compatible; switch to netkit on kernel >= 6.6 if desired
        "datapathMode": "veth",
        "masquerade": True,
    },
    "cluster": {"name": ""},          # filled dynamically
    "enableIPv4Masquerade": True,
    "enableIPv6Masquerade": False,
    "hubble": {
        "enabled": True,
        "relay": {"enabled": True},
        "ui": {"enabled": True},
    },
    "tolerations": [{"operator": "Exists"}],
    "ipam": {
        "mode": "cluster-pool",
        "operator": {
            "clusterPoolIPv4PodCIDRList": ["192.168.0.0/16"],
            "clusterPoolIPv4MaskSize": 24,
        },
    },
    "ipv4NativeRoutingCIDR": "",       # filled dynamically (private network CIDR)
    "k8sServiceHost": "",              # filled dynamically (control-plane LB IP)
    "k8sServicePort": 6443,            # kubeadm default (EKS uses 443)
    "kubeProxyReplacement": True,
    "loadBalancer": {
        "algorithm": "maglev",
        # hybrid mode: XDP for in-cluster, masquerade for external -- no DSR requirement
        "mode": "hybrid",
    },
    "nodePort": {"enabled": True},
    "operator": {
        "prometheus": {"enabled": True},
        "replicas": 1,
        "tolerations": [
            {
                "effect": "NoSchedule",
                "key": "node-type",
                "operator": "Equal",
                "value": "core",
            }
        ],
    },
    "prometheus": {"enabled": True},
    "routingMode": "native",
}

_DEFAULT_COREDNS_VALUES: Dict[str, Any] = {
    "replicaCount": 2,
    "service": {"type": "ClusterIP"},
    "resources": {
        "limits": {"memory": "170Mi", "cpu": "100m"},
        "requests": {"memory": "70Mi", "cpu": "100m"},
    },
    "podAnnotations": {
        "prometheus.io/port": "9153",
        "prometheus.io/scrape": "true",
    },
    "tolerations": [
        {
            "key": "node-type",
            "operator": "Equal",
            "value": "core",
            "effect": "NoSchedule",
        }
    ],
    "servers": [
        {
            "zones": [{"zone": "."}],
            "port": 53,
            "plugins": [
                {"name": "errors"},
                {"name": "health", "configBlock": "lameduck 5s"},
                {"name": "ready"},
                {
                    "name": "kubernetes",
                    "parameters": "cluster.local in-addr.arpa ip6.arpa",
                    "configBlock": "pods insecure\nfallthrough in-addr.arpa ip6.arpa\nttl 30",
                },
                {"name": "prometheus", "parameters": "0.0.0.0:9153"},
                {"name": "forward", "parameters": ". /etc/resolv.conf", "configBlock": "max_concurrent 1000"},
                {"name": "cache", "parameters": 30},
                {"name": "loop"},
                {"name": "reload"},
                {"name": "loadbalance"},
            ],
        }
    ],
}

_DEFAULT_FLUX_VALUES: Dict[str, Any] = {
    "installCRDs": True,
    **{
        ctrl: {
            "resources": {"limits": {"memory": "170Mi"}, "requests": {"memory": "170Mi"}},
            "tolerations": [
                {"key": "node-type", "operator": "Equal", "value": "core", "effect": "NoSchedule"}
            ],
        }
        for ctrl in [
            "notificationController",
            "sourceController",
            "kustomizeController",
            "helmController",
            "imageReflectionController",
            "imageAutomationController",
        ]
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml_mapping(file_path: str, component: str) -> Dict[str, Any]:
    try:
        with open(file_path, "r") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        pulumi.log.warn(f"{component} values file {file_path} not found, using defaults.")
        return {}
    except Exception as exc:
        pulumi.log.warn(f"Failed to load {component} values from {file_path}: {exc}.")
        return {}

    if not isinstance(data, dict):
        return {}

    # Support reading values directly from a HelmRelease resource
    if data.get("kind") == "HelmRelease" and "spec" in data:
        return data["spec"].get("values", {})

    return data


def _decrypt_sops(file_path: str, description: str) -> Optional[Dict[str, Any]]:
    if not file_path or not os.path.isfile(file_path):
        return None
    try:
        result = subprocess.run(
            ["sops", "-d", file_path], check=True, capture_output=True, text=True
        )
        data = yaml.safe_load(result.stdout) or {}
        return data if isinstance(data, dict) else None
    except Exception as exc:
        pulumi.log.warn(f"Failed to decrypt {description}: {exc}.")
        return None


def _k8s_provider(name: str, kubeconfig: pulumi.Input[str], depends_on: List[Any]) -> k8s.Provider:
    """Create a plain kubeconfig-based k8s provider (no AWS exec plugin)."""
    return k8s.Provider(
        name,
        kubeconfig=kubeconfig,
        opts=pulumi.ResourceOptions(depends_on=depends_on),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_kubernetes_addons(
    cluster_name: str,
    cluster_endpoint: pulumi.Input[str],
    private_network_cidr: str,
    pod_cidr: str,
    enable_cilium: bool,
    enable_coredns: bool,
    k8s_provider: k8s.Provider,
    cilium_values_path: Optional[str] = None,
    coredns_values_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Install Cilium and CoreDNS on the workload cluster."""
    result: Dict[str, Any] = {}

    cilium_base = _load_yaml_mapping(cilium_values_path or "", "Cilium") if cilium_values_path else {}
    coredns_base = _load_yaml_mapping(coredns_values_path or "", "CoreDNS") if coredns_values_path else {}

    if enable_cilium:
        def _build_cilium_values(args: List[str]) -> Dict[str, Any]:
            endpoint, cidr, name = args
            vals = deepcopy(cilium_base) if cilium_base else deepcopy(_DEFAULT_CILIUM_VALUES)
            vals["k8sServiceHost"] = endpoint
            vals["k8sServicePort"] = 6443
            vals["ipv4NativeRoutingCIDR"] = private_network_cidr
            vals["cluster"]["name"] = name
            vals.setdefault("ipam", {}).setdefault("operator", {})[
                "clusterPoolIPv4PodCIDRList"
            ] = [cidr]
            return vals

        cilium_values = pulumi.Output.all(
            cluster_endpoint, pod_cidr, cluster_name
        ).apply(_build_cilium_values)

        cilium_release = k8s.helm.v3.Release(
            "cilium",
            name="cilium",
            chart="cilium",
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(repo="https://helm.cilium.io/"),
            version="1.18.3",
            namespace="kube-system",
            values=cilium_values,
            skip_await=True,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                ignore_changes=["values"],
                retain_on_delete=True,
            ),
        )
        result["cilium_release"] = cilium_release

    if enable_coredns:
        coredns_values = deepcopy(coredns_base) if coredns_base else deepcopy(_DEFAULT_COREDNS_VALUES)
        deps = []
        if "cilium_release" in result:
            deps.append(result["cilium_release"])

        coredns_release = k8s.helm.v3.Release(
            "coredns",
            name="coredns",
            chart="coredns",
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(repo="https://coredns.github.io/helm/"),
            version="1.45.0",
            namespace="kube-system",
            values=coredns_values,
            skip_await=True,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=deps,
                retain_on_delete=True,
            ),
        )
        result["coredns_release"] = coredns_release

        service_id = coredns_release.status.apply(lambda _: "kube-system/coredns")
        coredns_svc = k8s.core.v1.Service.get(
            "coredns-service",
            service_id,
            opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[coredns_release]),
        )
        result["coredns_service"] = coredns_svc
        result["coredns_cluster_ip"] = coredns_svc.spec.apply(
            lambda s: s.cluster_ip if s else None
        )

    return result


def bootstrap_flux(
    cluster_name: str,
    cluster_endpoint: pulumi.Input[str],
    mgmt_kubeconfig: pulumi.Input[str],
    k8s_provider: k8s.Provider,
    enable_flux: bool,
    *,
    flux_values_path: Optional[str] = None,
    flux_git_url: Optional[str] = None,
    flux_git_branch: Optional[str] = None,
    flux_git_path: Optional[str] = None,
    flux_git_secret_name: Optional[str] = None,
    flux_git_secret_values_path: Optional[str] = None,
    flux_sops_secret_name: Optional[str] = None,
    flux_git_interval: Optional[str] = None,
    flux_kustomization_interval: Optional[str] = None,
    additional_dependencies: Optional[List[pulumi.Resource]] = None,
) -> Dict[str, Any]:
    """Bootstrap Flux on the workload cluster and wire up GitRepository + Kustomization."""
    if not enable_flux:
        return {}

    deps: List[pulumi.Resource] = list(additional_dependencies or [])
    result: Dict[str, Any] = {}

    flux_base = _load_yaml_mapping(flux_values_path or "", "Flux") if flux_values_path else {}
    flux_values = deepcopy(flux_base) if flux_base else deepcopy(_DEFAULT_FLUX_VALUES)

    flux_ns = k8s.core.v1.Namespace(
        "flux-system",
        metadata={"name": "flux-system"},
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=deps,
            retain_on_delete=True,
        ),
    )
    result["flux_namespace"] = flux_ns

    # ConfigMap consumed by Flux postBuild variable substitution
    infra_outputs_cm = k8s.core.v1.ConfigMap(
        "infra-outputs",
        metadata=k8s.meta.v1.ObjectMetaArgs(name="infra-outputs", namespace="flux-system"),
        data={
            "CLUSTER_NAME": cluster_name,
            "CLUSTER_ENDPOINT": cluster_endpoint,
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[flux_ns],
            ignore_changes=["metadata.annotations", "metadata.labels", "metadata.resourceVersion"],
            retain_on_delete=True,
        ),
    )
    result["infra_outputs_configmap"] = infra_outputs_cm

    # Management cluster kubeconfig Secret for Cluster Autoscaler.
    # CA runs on the workload cluster but must reach the management cluster to
    # scale MachineDeployments.
    mgmt_kubeconfig_secret = k8s.core.v1.Secret(
        "mgmt-cluster-kubeconfig",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="mgmt-cluster-kubeconfig",
            namespace="kube-system",
        ),
        string_data={"kubeconfig": mgmt_kubeconfig},
        type="Opaque",
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[flux_ns],
            additional_secret_outputs=["stringData"],
            retain_on_delete=True,
        ),
    )
    result["mgmt_kubeconfig_secret"] = mgmt_kubeconfig_secret

    # SOPS age key secret
    flux_sops_secret = None
    if flux_sops_secret_name:
        sops_key_path = os.environ.get("SOPS_AGE_KEY_FILE")
        if sops_key_path and os.path.isfile(sops_key_path):
            with open(sops_key_path, "r") as f:
                age_key = f.read()
            flux_sops_secret = k8s.core.v1.Secret(
                flux_sops_secret_name,
                metadata={"name": flux_sops_secret_name, "namespace": "flux-system"},
                string_data={"age.agekey": age_key},
                type="Opaque",
                opts=pulumi.ResourceOptions(
                    provider=k8s_provider,
                    depends_on=[flux_ns],
                    retain_on_delete=True,
                ),
            )
            result["flux_sops_secret"] = flux_sops_secret
        else:
            pulumi.log.warn("SOPS_AGE_KEY_FILE not set or missing — skipping SOPS secret.")

    # Git credentials secret
    flux_git_secret = None
    if flux_git_secret_name and flux_git_secret_values_path:
        secret_data = _decrypt_sops(flux_git_secret_values_path, "Flux Git secret")
        if secret_data:
            github = secret_data.get("github", {})
            username = github.get("username")
            token = github.get("token")
            if username and token:
                flux_git_secret = k8s.core.v1.Secret(
                    flux_git_secret_name,
                    metadata={"name": flux_git_secret_name, "namespace": "flux-system"},
                    type="Opaque",
                    string_data={
                        "username": username.strip(),
                        "password": token.strip(),
                    },
                    opts=pulumi.ResourceOptions(
                        provider=k8s_provider,
                        depends_on=[flux_ns],
                        retain_on_delete=True,
                    ),
                )
                result["flux_git_secret"] = flux_git_secret

    release_deps = [flux_ns, infra_outputs_cm, mgmt_kubeconfig_secret, *deps]
    if flux_sops_secret:
        release_deps.append(flux_sops_secret)
    if flux_git_secret:
        release_deps.append(flux_git_secret)

    flux_release = k8s.helm.v3.Release(
        "fluxcd",
        name="fluxcd",
        chart="flux2",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://fluxcd-community.github.io/helm-charts"
        ),
        version="2.17.1",
        namespace="flux-system",
        values=flux_values,
        skip_await=True,
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=release_deps,
            ignore_changes=["values"],
            retain_on_delete=True,
        ),
    )
    result["flux_release"] = flux_release

    if flux_git_url:
        git_repo_spec: Dict[str, Any] = {
            "interval": flux_git_interval or "1m0s",
            "url": flux_git_url,
        }
        if flux_git_branch:
            git_repo_spec["ref"] = {"branch": flux_git_branch}
        if flux_git_secret_name:
            git_repo_spec["secretRef"] = {"name": flux_git_secret_name}

        git_repo = k8s.apiextensions.CustomResource(
            "flux-system-git-repository",
            api_version="source.toolkit.fluxcd.io/v1",
            kind="GitRepository",
            metadata={"name": "flux-system", "namespace": "flux-system"},
            spec=git_repo_spec,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=[*release_deps, flux_release],
                retain_on_delete=True,
            ),
        )
        result["flux_git_repository"] = git_repo

        ks_spec: Dict[str, Any] = {
            "interval": flux_kustomization_interval or "10m0s",
            "path": flux_git_path or "./",
            "prune": True,
            "sourceRef": {"kind": "GitRepository", "name": "flux-system"},
            "postBuild": {
                "substituteFrom": [
                    {"kind": "ConfigMap", "name": infra_outputs_cm.metadata["name"]}
                ]
            },
        }
        if flux_sops_secret_name:
            ks_spec["decryption"] = {
                "provider": "sops",
                "secretRef": {"name": flux_sops_secret_name},
            }

        flux_ks = k8s.apiextensions.CustomResource(
            "flux-system-kustomization",
            api_version="kustomize.toolkit.fluxcd.io/v1",
            kind="Kustomization",
            metadata={"name": "flux-system", "namespace": "flux-system"},
            spec=ks_spec,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=[git_repo, flux_release],
                retain_on_delete=True,
            ),
        )
        result["flux_kustomization"] = flux_ks

    return result
