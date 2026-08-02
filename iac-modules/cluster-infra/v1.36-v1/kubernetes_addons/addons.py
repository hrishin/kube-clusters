"""
Kubernetes Add-ons (Cilium CNI and CoreDNS)
Equivalent to terraform/modules/kubernetes-addons
"""

import os
import subprocess
from copy import deepcopy
from typing import Any, Dict, List, Optional

import pulumi
import pulumi_kubernetes as k8s
import yaml


# Fallback used only when iac-modules/extensions/cilium/v1.18.3-v1/base/release.yaml
# cannot be read. Keep in sync with that file.
_DEFAULT_CILIUM_VALUES_BASE: Dict[str, Any] = {
    "bpf": {
        "masquerade": True,
    },
    "cluster": {
        "name": "",
    },
    "enableIPv4Masquerade": True,
    "hubble": {
        "enabled": True,
        "relay": {
            "enabled": True,
            "tolerations": [
                {
                    "effect": "NoSchedule",
                    "key": "node-type",
                    "operator": "Equal",
                    "value": "core",
                }
            ],
        },
        "ui": {
            "enabled": True,
            "tolerations": [
                {
                    "effect": "NoSchedule",
                    "key": "node-type",
                    "operator": "Equal",
                    "value": "core",
                }
            ],
        },
    },
    "tolerations": [{"operator": "Exists"}],
    # --- ENI IPAM configuration (commented out) ---
    # "ipam": {
    #     "eni": {
    #         "enabled": True,
    #         "securityGroupTags": {},
    #         "subnetTags": {},
    #     },
    #     "mode": "eni",
    # },
    # --- Cluster Scope IPAM mode ---
    "ipam": {
        "mode": "cluster-pool",
        "operator": {
            "clusterPoolIPv4PodCIDRList": ["192.168.0.0/16"],
            "clusterPoolIPv4MaskSize": 24,
        },
    },
    "k8sServiceHost": "",
    "kubeProxyReplacement": True,
    "loadBalancer": {
        "algorithm": "maglev",
    },
    "nodePort": {
        "enabled": True,
    },
    "operator": {
        "prometheus": {
            "enabled": True,
        },
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
    "prometheus": {
        "enabled": True,
    },
}

# EKS-specific settings applied unconditionally on top of base values.
# Must stay in sync with iac-modules/extensions/cilium/v1.18.3-v1/eks/release.yaml.
_EKS_CILIUM_OVERRIDES: Dict[str, Any] = {
    "bpf": {
        "datapathMode": "netkit",
    },
    "enableIPv6Masquerade": True,
    "image": {
        "repository": "quay.io/cilium/cilium",
        "tag": "v1.16.4",
    },
    "k8sServicePort": 443,
    "loadBalancer": {
        "mode": "snat",
    },
    "operator": {
        "image": {
            "repository": "quay.io/cilium/operator",
            "tag": "v1.16.4",
        },
    },
    "routingMode": "tunnel",
    "tunnelProtocol": "vxlan",
}


_DEFAULT_COREDNS_VALUES: Dict[str, Any] = {
    "image": {
        "repository": "coredns/coredns",
        "tag": "1.11.1",
    },
    "replicaCount": 2,
    "service": {
        "type": "ClusterIP"
    },
    "resources": {
        "limits": {
            "memory": "170Mi",
            "cpu": "100m",
        },
        "requests": {
            "memory": "70Mi",
            "cpu": "100m",
        },
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
            "zones": [
                {
                    "zone": ".",
                }
            ],
            "port": 53,
            "plugins": [
                {"name": "errors"},
                {
                    "name": "health",
                    "configBlock": "lameduck 5s",
                },
                {"name": "ready"},
                {
                    "name": "kubernetes",
                    "parameters": "cluster.local in-addr.arpa ip6.arpa",
                    "configBlock": "pods insecure\nfallthrough in-addr.arpa ip6.arpa\nttl 30",
                },
                {
                    "name": "prometheus",
                    "parameters": "0.0.0.0:9153",
                },
                {
                    "name": "forward",
                    "parameters": ". /etc/resolv.conf",
                    "configBlock": "max_concurrent 1000",
                },
                {
                    "name": "cache",
                    "parameters": 30,
                },
                {"name": "loop"},
                {"name": "reload"},
                {"name": "loadbalance"},
            ],
        }
    ],
}


_DEFAULT_FLUX_VALUES: Dict[str, Any] = {
    "installCRDs": True,
    "notificationController": {
        "resources": {
            "limits": {"memory": "170Mi"},
            "requests": {"memory": "170Mi"},
        },
        "tolerations": [
            {
                "key": "node-type",
                "operator": "Equal",
                "value": "core",
                "effect": "NoSchedule",
            }
        ],
    },
    "sourceController": {
        "resources": {
            "limits": {"memory": "170Mi"},
            "requests": {"memory": "170Mi"},
        },
        "tolerations": [
            {
                "key": "node-type",
                "operator": "Equal",
                "value": "core",
                "effect": "NoSchedule",
            }
        ],
    },
    "sourceWatcher": {
        "resources": {
            "limits": {"memory": "170Mi"},
            "requests": {"memory": "170Mi"},
        },
        "tolerations": [
            {
                "key": "node-type",
                "operator": "Equal",
                "value": "core",
                "effect": "NoSchedule",
            }
        ],
    },
    "kustomizeController": {
        "resources": {
            "limits": {"memory": "170Mi"},
            "requests": {"memory": "170Mi"},
        },
        "tolerations": [
            {
                "key": "node-type",
                "operator": "Equal",
                "value": "core",
                "effect": "NoSchedule",
            }
        ],
    },
    "imageReflectionController": {
        "resources": {
            "limits": {"memory": "170Mi"},
            "requests": {"memory": "170Mi"},
        },
        "tolerations": [
            {
                "key": "node-type",
                "operator": "Equal",
                "value": "core",
                "effect": "NoSchedule",
            }
        ],
    },
    "imageAutomationController": {
        "resources": {
            "limits": {"memory": "170Mi"},
            "requests": {"memory": "170Mi"},
        },
        "tolerations": [
            {
                "key": "node-type",
                "operator": "Equal",
                "value": "core",
                "effect": "NoSchedule",
            }
        ],
    },
    "helmController": {
        "resources": {
            "limits": {"memory": "170Mi"},
            "requests": {"memory": "170Mi"},
        },
        "tolerations": [
            {
                "key": "node-type",
                "operator": "Equal",
                "value": "core",
                "effect": "NoSchedule",
            }
        ],
    },
}


def _load_yaml_mapping(file_path: str, component_name: str) -> Dict[str, Any]:
    try:
        with open(file_path, "r") as file:
            data = yaml.safe_load(file) or {}
    except FileNotFoundError:
        pulumi.log.warn(f"{component_name} values file {file_path} not found. Falling back to defaults.")
        return {}
    except Exception as exc:  # noqa: BLE001
        pulumi.log.warn(
            f"Failed to load {component_name} values from {file_path}: {exc}. Falling back to defaults."
        )
        return {}

    if not isinstance(data, dict):
        pulumi.log.warn(
            f"{component_name} values file {file_path} must deserialize to a mapping. Falling back to defaults."
        )
        return {}

    # Support reading values directly from a HelmRelease resource
    if data.get("kind") == "HelmRelease" and "spec" in data:
        return data["spec"].get("values", {})

    return data


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base, returning base (mutated in place)."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def _set_nested_value(target: Dict[str, Any], keys: List[str], value: Any) -> None:
    current: Dict[str, Any] = target
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _decrypt_sops_mapping(file_path: str, description: str) -> Optional[Dict[str, Any]]:
    """
    Decrypt a SOPS-managed file and deserialize it into a mapping.
    """

    if not file_path:
        return None

    if not os.path.isfile(file_path):
        pulumi.log.warn(f"{description} encrypted values file {file_path} not found. Skipping.")
        return None

    try:
        completed = subprocess.run(
            ["sops", "-d", file_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pulumi.log.warn("sops binary not found in PATH. Skipping decryption for Flux Git secret.")
        return None
    except subprocess.CalledProcessError as exc:
        pulumi.log.warn(
            f"Failed to decrypt {description} values from {file_path}: {exc.stderr or exc}. Skipping."
        )
        return None

    try:
        data = yaml.safe_load(completed.stdout) or {}
    except Exception as exc:  # noqa: BLE001
        pulumi.log.warn(
            f"Failed to deserialize decrypted {description} values from {file_path}: {exc}. Skipping."
        )
        return None

    if not isinstance(data, dict):
        pulumi.log.warn(
            f"Decrypted {description} values from {file_path} must deserialize to a mapping. Skipping."
        )
        return None

    return data


def create_kubernetes_addons(
    cluster_name: str,
    cluster_endpoint: pulumi.Output,
    cluster_ca_certificate: pulumi.Output,
    pod_cidr_range: str,
    enable_cilium: bool,
    enable_coredns: bool,
    cluster: Any,
    auth_dependencies: List[Any],
    region: str,
    cilium_values_path: Optional[str] = None,
    coredns_values_path: Optional[str] = None,
) -> dict:
    """
    Install Kubernetes add-ons via Helm
    
    Args:
        cluster_name: Name of the EKS cluster
        cluster_endpoint: Cluster endpoint URL
        cluster_ca_certificate: Base64-encoded cluster CA certificate
        pod_cidr_range: CIDR range for pods
        enable_cilium: Whether to install Cilium CNI
        enable_coredns: Whether to install CoreDNS
        cluster: EKS cluster resource (for dependency)
        auth_dependencies: List of auth resources (Access Entries / policy associations) to depend on
        region: AWS region where the cluster lives
        cilium_values_path: Optional path to YAML file with base values for the Cilium Helm release
        coredns_values_path: Optional path to YAML file with base values for the CoreDNS Helm release
    Returns:
        Dictionary containing addon resources
    """
    
    # Create Kubernetes provider
    k8s_provider = k8s.Provider(
        f"{cluster_name}-addons-k8s-provider",
        kubeconfig=pulumi.Output.all(
            cluster_endpoint,
            cluster_ca_certificate,
        ).apply(lambda args: f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {args[1]}
    server: {args[0]}
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
      command: aws
      args:
        - eks
        - get-token
        - --cluster-name
        - {cluster_name}
        - --region
        - {region}
"""),
        opts=pulumi.ResourceOptions(depends_on=[cluster, *auth_dependencies]),
    )
    
    result = {}

    cilium_base_values: Dict[str, Any] = {}
    if enable_cilium and cilium_values_path:
        cilium_base_values = _load_yaml_mapping(cilium_values_path, "Cilium")
    coredns_base_values: Dict[str, Any] = {}
    if enable_coredns and coredns_values_path:
        coredns_base_values = _load_yaml_mapping(coredns_values_path, "CoreDNS")
    # Install Cilium CNI
    if enable_cilium:
        cluster_host = cluster_endpoint.apply(lambda ep: ep.replace("https://", ""))
        
        cilium_values_base = deepcopy(cilium_base_values) if cilium_base_values else {}

        def build_cilium_values(args: List[str]) -> Dict[str, Any]:
            cluster_host_value, pod_cidr_value, cluster_name_value = args

            values = deepcopy(cilium_values_base) if cilium_values_base else deepcopy(_DEFAULT_CILIUM_VALUES_BASE)

            # Apply EKS-specific static overrides unconditionally so the
            # bootstrap install always matches what Flux reconciles from
            # iac-modules/extensions/cilium/v1.18.3-v1/eks/release.yaml.
            values = _deep_merge(values, deepcopy(_EKS_CILIUM_OVERRIDES))

            # --- ENI tag injection (commented out) ---
            # cluster_tag_value = {f"kubernetes.io/cluster/{cluster_name_value}": "owned"}
            # _set_nested_value(values, ["ipam", "eni", "subnetTags"], cluster_tag_value)
            # _set_nested_value(values, ["ipam", "eni", "securityGroupTags"], cluster_tag_value)
            # _set_nested_value(values, ["eni", "subnetTags"], cluster_tag_value)
            # _set_nested_value(values, ["eni", "securityGroupTags"], cluster_tag_value)

            _set_nested_value(values, ["k8sServiceHost"], cluster_host_value)
            _set_nested_value(values, ["cluster", "name"], cluster_name_value)

            # Cluster Scope IPAM: set pod CIDR from config
            _set_nested_value(values, ["ipam", "operator", "clusterPoolIPv4PodCIDRList"], [pod_cidr_value])

            return values

        cilium_values = pulumi.Output.all(cluster_host, pod_cidr_range, cluster_name).apply(build_cilium_values)
        
        cilium_release = k8s.helm.v3.Release(
            "cilium",
            name="cilium",
            chart="cilium",
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(
                repo="https://helm.cilium.io/",
            ),
            version="1.18.3",
            namespace="kube-system",
            values=cilium_values,
            skip_await=True,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=[cluster, *auth_dependencies],
                ignore_changes=["values"],
                retain_on_delete=True,
            ),
        )
        
        result["cilium_release"] = cilium_release
    
    # Install CoreDNS
    if enable_coredns:
        coredns_values = deepcopy(coredns_base_values) if coredns_base_values else deepcopy(_DEFAULT_COREDNS_VALUES)
        
        deps = [cluster, *auth_dependencies]
        if "cilium_release" in result:
            deps.append(result["cilium_release"])
        
        coredns_release = k8s.helm.v3.Release(
            "coredns",
            name="coredns",
            chart="coredns",
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(
                repo="https://coredns.github.io/helm/",
            ),
            version="1.45.0",
            namespace="kube-system",
            values=coredns_values,
            skip_await=True,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=deps,
                # ignore_changes=["values"],
                retain_on_delete=True,
            ),
        )
        
        result["coredns_release"] = coredns_release

        # Use an output from the release to defer the .get lookup during preview
        service_id = coredns_release.status.apply(lambda _: "kube-system/coredns")
        
        coredns_service = k8s.core.v1.Service.get(
            "coredns-service",
            service_id,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=[coredns_release],
            ),
        )
        result["coredns_service"] = coredns_service
        result["coredns_cluster_ip"] = coredns_service.spec.apply(
            lambda spec: spec.cluster_ip if spec else None
        )
    
    return result


def bootstrap_flux(
    cluster_name: str,
    cluster_endpoint: pulumi.Output,
    cluster_ca_certificate: pulumi.Output,
    cluster: Any,
    auth_dependencies: List[Any],
    region: str,
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
    aws_lbc_role_arn: Optional[pulumi.Output] = None,
    nlb_dns: Optional[pulumi.Output] = None,
    additional_dependencies: Optional[List[pulumi.Resource]] = None,
) -> Dict[str, Any]:
    """
    Bootstrap Flux components on the cluster.
    """
    if not enable_flux:
        return {}

    dependencies: List[pulumi.Resource] = [cluster, *auth_dependencies]
    if additional_dependencies:
        dependencies.extend(additional_dependencies)

    k8s_provider = k8s.Provider(
        f"{cluster_name}-flux-k8s-provider",
        kubeconfig=pulumi.Output.all(
            cluster_endpoint,
            cluster_ca_certificate,
        ).apply(lambda args: f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {args[1]}
    server: {args[0]}
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
      command: aws
      args:
        - eks
        - get-token
        - --cluster-name
        - {cluster_name}
        - --region
        - {region}
"""),
        opts=pulumi.ResourceOptions(depends_on=dependencies),
    )

    flux_base_values: Dict[str, Any] = {}
    if flux_values_path:
        flux_base_values = _load_yaml_mapping(flux_values_path, "Flux")
    flux_values = deepcopy(flux_base_values) if flux_base_values else deepcopy(_DEFAULT_FLUX_VALUES)

    result: Dict[str, Any] = {}

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

    cluster_host = cluster_endpoint.apply(lambda ep: ep.replace("https://", ""))
    infra_outputs_configmap = k8s.core.v1.ConfigMap(
        "infra-outputs",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="infra-outputs",
            namespace="flux-system",
        ),
        data={
            "CLUSTER_NAME": cluster_name,
            "CLUSTER_ENDPOINT": cluster_host,
            "AWS_LBC_ROLE_ARN": aws_lbc_role_arn or "",
            "NLB_DNS": nlb_dns or "",
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[flux_namespace],
            ignore_changes=[
                "metadata.annotations",
                "metadata.labels",
                "metadata.generation",
                "metadata.resourceVersion",
            ],
            retain_on_delete=True,
        ),
    )
    result["infra_outputs_configmap"] = infra_outputs_configmap

    flux_sops_secret = None
    if flux_sops_secret_name:
        sops_age_key_path = os.environ.get("SOPS_AGE_KEY_FILE")
        if not sops_age_key_path:
            pulumi.log.warn(
                "Environment variable SOPS_AGE_KEY_FILE is not set. Skipping creation of the Flux SOPS secret."
            )
        else:
            try:
                with open(sops_age_key_path, "r", encoding="utf-8") as age_key_file:
                    sops_age_key_content = age_key_file.read()
            except FileNotFoundError:
                pulumi.log.warn(
                    f"SOPS Age key file {sops_age_key_path} not found. Skipping creation of the Flux SOPS secret."
                )
            except Exception as exc:  # noqa: BLE001
                pulumi.log.warn(
                    f"Failed to read SOPS Age key file {sops_age_key_path}: {exc}. Skipping creation of the Flux SOPS secret."
                )
            else:
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

    flux_git_secret = None
    if flux_git_secret_name and flux_git_secret_values_path:
        secret_values = _decrypt_sops_mapping(
            flux_git_secret_values_path,
            "Flux Git secret",
        )
        if secret_values:
            github_values = secret_values.get("github") or {}
            username = github_values.get("username")
            token = github_values.get("token")

            if not username or not token:
                pulumi.log.warn(
                    f"Flux Git secret values file {flux_git_secret_values_path} must contain both username and token."
                )

            if github_values:
                string_data: Dict[str, pulumi.Input[str]] = {
                    "username": username.strip() if isinstance(username, str) else username,
                    "password": token.strip() if isinstance(token, str) else token,
                }

                flux_git_secret = k8s.core.v1.Secret(
                    flux_git_secret_name,
                    metadata={"name": flux_git_secret_name, "namespace": "flux-system"},
                    type="Opaque",
                    opts=pulumi.ResourceOptions(
                        provider=k8s_provider,
                        depends_on=[flux_namespace],
                        retain_on_delete=True,
                    ),
                    string_data=string_data,
                )
                result["flux_git_secret"] = flux_git_secret

    release_dependencies: List[pulumi.Resource] = [
        flux_namespace,
        infra_outputs_configmap,
        *dependencies,
    ]
    if flux_sops_secret:
        release_dependencies.append(flux_sops_secret)
    if flux_git_secret:
        release_dependencies.append(flux_git_secret)

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
            depends_on=release_dependencies,
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

        git_repo_dependencies = release_dependencies + [flux_release]

        flux_git_repository = k8s.apiextensions.CustomResource(
            "flux-system-git-repository",
            api_version="source.toolkit.fluxcd.io/v1",
            kind="GitRepository",
            metadata={"name": "flux-system", "namespace": "flux-system"},
            spec=git_repo_spec,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=git_repo_dependencies,
                retain_on_delete=True,
            ),
        )
        result["flux_git_repository"] = flux_git_repository

        kustomization_spec: Dict[str, Any] = {
            "interval": flux_kustomization_interval or "10m0s",
            "path": flux_git_path or "./",
            "prune": True,
            "sourceRef": {
                "kind": "GitRepository",
                "name": "flux-system",
            },
            "postBuild": {
                "substituteFrom": [{
                    "kind": "ConfigMap",
                    "name": infra_outputs_configmap.metadata["name"],
                }],
            },
        }
        if flux_sops_secret_name:
            kustomization_spec["decryption"] = {
                "provider": "sops",
                "secretRef": {"name": flux_sops_secret_name},
            }

        flux_kustomization = k8s.apiextensions.CustomResource(
            "flux-system-kustomization",
            api_version="kustomize.toolkit.fluxcd.io/v1",
            kind="Kustomization",
            metadata={"name": "flux-system", "namespace": "flux-system"},
            spec=kustomization_spec,
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=[flux_git_repository, flux_release],
                retain_on_delete=True,
            ),
        )
        result["flux_kustomization"] = flux_kustomization

    return result

