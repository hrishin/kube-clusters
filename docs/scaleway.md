# Scaleway CAPS Cluster Setup

This document covers provisioning the `scw-alpha` Scaleway cluster using Pulumi and Cluster API Provider Scaleway (CAPS).

## Prerequisites

Install these tools before starting:

| Tool | Purpose |
|---|---|
| `pulumi` | Infrastructure provisioning |
| `kind` | Local management cluster |
| `clusterctl` | Cluster API CLI |
| `kubectl` | Kubernetes CLI |
| `helm` | Post-deploy verification |
| `sops` + `age` | Secret decryption |
| `python3` | Pulumi runtime |

```bash
# Verify all tools are available
for cmd in pulumi kind clusterctl kubectl helm sops age; do
  command -v $cmd && echo "$cmd ✓" || echo "$cmd MISSING"
done
```

## 1. Python environment

```bash
cd <repo-root>
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install pulumi-command
```

## 2. Pulumi login & stack

```bash
pulumi login          # or: pulumi login --local for local state
cd clusters/scw-alpha/infra
pulumi stack init scw-alpha   # skip if stack already exists
```

## 3. Scaleway credentials

Secrets are stored in Pulumi encrypted state — never in `config.yaml`.

```bash
pulumi config set --secret scw_project_id  <your-project-id>
pulumi config set --secret scw_access_key  <your-access-key>
pulumi config set --secret scw_secret_key  <your-secret-key>
```

Get these from the Scaleway Console → IAM → API Keys.

## 4. SOPS age key

Flux uses SOPS to decrypt `config/config.enc.yaml`, which contains the GitHub token for the `flux-system` GitRepository.

```bash
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt

# Verify the key exists
cat $SOPS_AGE_KEY_FILE | head -1
```

The age public key must already be in `.sops.yaml` at the repo root. If setting up fresh, generate a key, add the public key to `.sops.yaml`, then re-encrypt `config/config.enc.yaml`.

## 5. Review `config.yaml`

Key settings in `clusters/scw-alpha/config.yaml` to verify before deploying:

```yaml
cluster_name: scw-alpha
kubernetes_version: v1.34.3
scw_region: fr-par
private_network_cidr: 172.16.0.0/22   # must match cilium ipv4NativeRoutingCIDR
pod_cidr_range: 192.168.0.0/16
flux_git_url: https://github.com/hrishin/kube-clusters
flux_git_branch: main
flux_git_path: clusters/scw-alpha/extensions
```

## 6. Deploy

```bash
cd clusters/scw-alpha/infra
pulumi preview   # inspect the plan first
pulumi up
```

Pulumi runs the following steps in sequence:

1. Creates a local `kind` cluster named `caps-mgmt-scw-alpha`
2. Runs `clusterctl init --infrastructure scaleway` on it
3. Applies CAPI/CAPS resources (Cluster, KubeadmControlPlane, MachineDeployments)
4. Waits for the workload cluster control plane to become Ready
5. Installs **Cilium** (CNI must be present before nodes reach Ready)
6. Installs **CoreDNS**
7. Bootstraps **Flux** — from this point GitOps takes over (CCM, CSI, Cluster Autoscaler, etc.)

Total time: ~15–25 minutes.

## 7. Access the cluster

```bash
# Export the workload kubeconfig
pulumi stack output workload_kubeconfig --show-secrets > ~/.kube/scw-alpha.yaml
export KUBECONFIG=~/.kube/scw-alpha.yaml

# Verify nodes
kubectl get nodes

# Check Flux is reconciling
kubectl -n flux-system get kustomizations
kubectl -n flux-system get gitrepositories
```

## What Flux manages after bootstrap

Once Flux is running it reconciles everything under `clusters/scw-alpha/extensions/`:

| Component | Source path |
|---|---|
| Cilium (full values) | `iac-modules/extensions/cilium/v1.18.3-v1/scaleway/` |
| Scaleway CCM | `iac-modules/extensions/scaleway-ccm/` |
| Scaleway CSI | `iac-modules/extensions/scaleway-csi/` |
| Cluster Autoscaler | `iac-modules/extensions/cluster-autoscaler/` |
| Cert-Manager | `iac-modules/extensions/cert-manager/` |
| Grafana / Mimir | `iac-modules/extensions/grafana/`, `mimir/` |

## Teardown

```bash
cd clusters/scw-alpha/infra
pulumi destroy
```

The `kind` management cluster is deleted automatically via Pulumi's delete script.
