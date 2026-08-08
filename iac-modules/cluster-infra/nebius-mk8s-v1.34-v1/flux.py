import base64
import os
import subprocess
from copy import deepcopy
from typing import Any, Dict, List, Optional

import pulumi
import pulumi_kubernetes as k8s
import pulumi_nebius as nebius
import yaml


_DEFAULT_FLUX_VALUES: Dict[str, Any] = {
    "installCRDs": True,
    "notificationController": {
        "resources": {"limits": {"memory": "170Mi"}, "requests": {"memory": "170Mi"}},
        "tolerations": [{"key": "node-type", "operator": "Equal", "value": "core", "effect": "NoSchedule"}],
        "nodeSelector": {"node-type": "core"},
    },
    "sourceController": {
        "resources": {"limits": {"memory": "512Mi"}, "requests": {"memory": "512Mi"}},
        "tolerations": [{"key": "node-type", "operator": "Equal", "value": "core", "effect": "NoSchedule"}],
        "nodeSelector": {"node-type": "core"},
    },
    "kustomizeController": {
        "resources": {"limits": {"memory": "170Mi"}, "requests": {"memory": "170Mi"}},
        "tolerations": [{"key": "node-type", "operator": "Equal", "value": "core", "effect": "NoSchedule"}],
        "nodeSelector": {"node-type": "core"},
    },
    "helmController": {
        "resources": {"limits": {"memory": "170Mi"}, "requests": {"memory": "170Mi"}},
        "tolerations": [{"key": "node-type", "operator": "Equal", "value": "core", "effect": "NoSchedule"}],
        "nodeSelector": {"node-type": "core"},
    },
    "imageReflectionController": {
        "resources": {"limits": {"memory": "170Mi"}, "requests": {"memory": "170Mi"}},
        "tolerations": [{"key": "node-type", "operator": "Equal", "value": "core", "effect": "NoSchedule"}],
        "nodeSelector": {"node-type": "core"},
    },
    "imageAutomationController": {
        "resources": {"limits": {"memory": "170Mi"}, "requests": {"memory": "170Mi"}},
        "tolerations": [{"key": "node-type", "operator": "Equal", "value": "core", "effect": "NoSchedule"}],
        "nodeSelector": {"node-type": "core"},
    },
}


def _load_yaml_mapping(file_path: str, component_name: str) -> Dict[str, Any]:
    try:
        with open(file_path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        pulumi.log.warn(f"{component_name} values file {file_path} not found. Using defaults.")
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("kind") == "HelmRelease" and "spec" in data:
        return data["spec"].get("values", {})
    return data


def _decrypt_sops_mapping(file_path: str, description: str) -> Optional[Dict[str, Any]]:
    if not file_path or not os.path.isfile(file_path):
        pulumi.log.warn(f"{description} file {file_path} not found. Skipping.")
        return None
    try:
        result = subprocess.run(["sops", "-d", file_path], check=True, capture_output=True, text=True)
        data = yaml.safe_load(result.stdout) or {}
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        pulumi.log.warn("sops not found in PATH. Skipping decryption.")
        return None
    except subprocess.CalledProcessError as exc:
        pulumi.log.warn(f"sops decryption failed for {description}: {exc.stderr}. Skipping.")
        return None


def _build_kubeconfig(cluster_name: str, endpoint: str, ca_pem: str) -> str:
    ca_b64 = base64.b64encode(ca_pem.encode()).decode()
    # Nebius returns the endpoint with https:// already included
    server = endpoint if endpoint.startswith("https://") else f"https://{endpoint}"
    # Delegate to the nebius CLI's own exec-credential plugin (same as what
    # `nebius mk8s v1 cluster get-credentials` writes into ~/.kube/config)
    # instead of embedding a token: client-go then fetches/refreshes the
    # token itself, so long-running applies can't outlive a static token.
    return f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {ca_b64}
    server: {server}
  name: {cluster_name}
contexts:
- context:
    cluster: {cluster_name}
    user: {cluster_name}
  name: {cluster_name}
current-context: {cluster_name}
kind: Config
preferences: {{}}
users:
- name: {cluster_name}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: nebius
      args:
      - mk8s
      - v1
      - cluster
      - get-token
      - --format
      - json
      interactiveMode: IfAvailable
"""


def bootstrap_flux(
    cluster_name: str,
    cluster: nebius.Mk8sV1Cluster,
    *,
    flux_values_path: Optional[str] = None,
    flux_git_url: str,
    flux_git_branch: str = "main",
    flux_git_path: str,
    flux_git_secret_name: str = "flux-system",
    flux_git_secret_values_path: Optional[str] = None,
    flux_sops_secret_name: Optional[str] = None,
    flux_git_interval: str = "1m0s",
    flux_kustomization_interval: str = "10m0s",
    additional_dependencies: Optional[List[pulumi.Resource]] = None,
) -> Dict[str, Any]:
    """
    Bootstrap Flux on a Nebius MK8s cluster.

    Mirrors iac-modules/cluster-infra/eks-v1.36-v1/kubernetes_addons/addons.py::bootstrap_flux
    but builds the kubeconfig from Nebius cluster status (endpoint + CA cert PEM) instead
    of using `aws eks get-token`.
    """
    dependencies: List[pulumi.Resource] = [cluster]
    if additional_dependencies:
        dependencies.extend(additional_dependencies)

    # Build kubeconfig from cluster status outputs (resolved at deploy time).
    # Auth is delegated to an exec-credential plugin (see _build_kubeconfig),
    # so no token needs to be fetched here.
    kubeconfig = pulumi.Output.all(
        cluster.status.control_plane.endpoints.public_endpoint,
        cluster.status.control_plane.auth.cluster_ca_certificate,
    ).apply(lambda args: _build_kubeconfig(cluster_name, args[0], args[1]))

    k8s_provider = k8s.Provider(
        f"{cluster_name}-flux-k8s-provider",
        kubeconfig=kubeconfig,
        opts=pulumi.ResourceOptions(depends_on=dependencies),
    )

    result: Dict[str, Any] = {"kubeconfig": kubeconfig, "k8s_provider": k8s_provider}

    flux_values_raw: Dict[str, Any] = {}
    if flux_values_path:
        flux_values_raw = _load_yaml_mapping(flux_values_path, "Flux")
    flux_values = deepcopy(flux_values_raw) if flux_values_raw else deepcopy(_DEFAULT_FLUX_VALUES)

    flux_namespace = k8s.core.v1.Namespace(
        "flux-system",
        metadata={"name": "flux-system"},
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=dependencies,
            retain_on_delete=True,
        ),
    )
    result["flux_namespace"] = flux_namespace

    cluster_endpoint = cluster.status.control_plane.endpoints.public_endpoint
    infra_outputs_configmap = k8s.core.v1.ConfigMap(
        "infra-outputs",
        metadata=k8s.meta.v1.ObjectMetaArgs(name="infra-outputs", namespace="flux-system"),
        data={
            "CLUSTER_NAME": cluster_name,
            "CLUSTER_ENDPOINT": cluster_endpoint.apply(
                lambda ep: ep.removeprefix("https://").removeprefix("http://")
            ),
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[flux_namespace],
            ignore_changes=["metadata.annotations", "metadata.labels", "metadata.resourceVersion"],
            retain_on_delete=True,
        ),
    )
    result["infra_outputs_configmap"] = infra_outputs_configmap

    flux_sops_secret = None
    if flux_sops_secret_name:
        sops_age_key_path = os.environ.get("SOPS_AGE_KEY_FILE")
        if not sops_age_key_path:
            pulumi.log.warn("SOPS_AGE_KEY_FILE not set. Skipping SOPS secret creation.")
        else:
            try:
                sops_age_key_content = open(sops_age_key_path).read()
                flux_sops_secret = k8s.core.v1.Secret(
                    flux_sops_secret_name,
                    metadata={"name": flux_sops_secret_name, "namespace": "flux-system"},
                    string_data={"age.agekey": sops_age_key_content},
                    type="Opaque",
                    opts=pulumi.ResourceOptions(
                        provider=k8s_provider,
                        depends_on=[flux_namespace],
                        retain_on_delete=True,
                    ),
                )
                result["flux_sops_secret"] = flux_sops_secret
            except FileNotFoundError:
                pulumi.log.warn(f"SOPS Age key file {sops_age_key_path} not found. Skipping.")

    flux_git_secret = None
    if flux_git_secret_name and flux_git_secret_values_path:
        secret_data = _decrypt_sops_mapping(flux_git_secret_values_path, "Flux Git secret")
        if secret_data:
            github = secret_data.get("github") or {}
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
                        depends_on=[flux_namespace],
                        retain_on_delete=True,
                    ),
                )
                result["flux_git_secret"] = flux_git_secret
            else:
                pulumi.log.warn("Flux Git secret missing username or token. Skipping.")

    release_deps: List[pulumi.Resource] = [flux_namespace, infra_outputs_configmap, *dependencies]
    if flux_sops_secret:
        release_deps.append(flux_sops_secret)
    if flux_git_secret:
        release_deps.append(flux_git_secret)

    flux_release = k8s.helm.v3.Release(
        "fluxcd",
        name="fluxcd",
        chart="flux2",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://fluxcd-community.github.io/helm-charts",
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

    git_repo_spec: Dict[str, Any] = {
        "interval": flux_git_interval,
        "url": flux_git_url,
    }
    if flux_git_branch:
        git_repo_spec["ref"] = {"branch": flux_git_branch}
    if flux_git_secret_name:
        git_repo_spec["secretRef"] = {"name": flux_git_secret_name}

    flux_git_repository = k8s.apiextensions.CustomResource(
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
    result["flux_git_repository"] = flux_git_repository

    flux_kustomization = k8s.apiextensions.CustomResource(
        "flux-system-kustomization",
        api_version="kustomize.toolkit.fluxcd.io/v1",
        kind="Kustomization",
        metadata={"name": "flux-system", "namespace": "flux-system"},
        spec={
            "interval": flux_kustomization_interval,
            "path": flux_git_path,
            "prune": True,
            "sourceRef": {"kind": "GitRepository", "name": "flux-system"},
            "postBuild": {
                "substituteFrom": [{"kind": "ConfigMap", "name": "infra-outputs"}],
            },
            **({"decryption": {"provider": "sops", "secretRef": {"name": flux_sops_secret_name}}}
               if flux_sops_secret_name else {}),
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[flux_git_repository, flux_release],
            retain_on_delete=True,
        ),
    )
    result["flux_kustomization"] = flux_kustomization

    return result
