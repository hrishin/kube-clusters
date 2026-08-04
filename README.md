## Goal

Opinionated, production-ready GPU cluster blueprints for **model serving, inference, and training** — spanning AI-native clouds and hyperscalers from a single IaC codebase.

The target clouds range from AI/neo clouds such as **Nebius** and **CoreWeave** (purpose-built GPU infrastructure, competitive spot pricing) to hyperscalers **AWS**, **Azure**, and **GCP** (breadth of managed services, global reach). Each provider gets its own Pulumi module and Flux extension layer; the cluster configuration schema and GitOps layout are intentionally kept uniform across all of them so workloads and tooling can move between providers with minimal friction.

## Kubernetes Infrastructure

This repository contains Pulumi programs and GitOps configuration for provisioning and managing Kubernetes clusters across **AWS EKS**, **Scaleway** (CAPS), and **Nebius AI Cloud** (MK8s).

## Supported Providers

| Provider | Cluster | Module |
|---|---|---|
| AWS EKS | `clusters/eks-alpha` | `iac-modules/cluster-infra/v1.36-v1` |
| Scaleway CAPS | `clusters/scw-alpha`, `clusters/scw-mgmt-alpha` | `iac-modules/cluster-infra/caps-v1` |
| Nebius MK8s | `clusters/nebius-alpha` | `iac-modules/cluster-infra/nebius-mk8s-v1` |

## What Gets Provisioned

**AWS EKS**
- VPC, subnets, internet/NAT gateways
- EKS control plane (API mode auth, OIDC)
- Self-managed node groups via launch templates + ASGs
- Cilium CNI, CoreDNS, Flux bootstrapped from `clusters/eks-alpha/extensions`
- Karpenter, cert-manager, kgateway via GitOps

**Scaleway CAPS**
- Local `kind` management cluster running CAPI + CAPS controllers
- Scaleway private network, control plane MachineDeployments, worker node groups
- Cilium CNI, CoreDNS, Flux bootstrapped from `clusters/scw-alpha/extensions`
- Scaleway CCM, CSI, Cluster Autoscaler via GitOps

**Nebius MK8s**
- Nebius VPC network + subnet
- Managed MK8s control plane (public endpoint, etcd)
- CPU node groups (`cpu-d3`, 16 vCPU / 64 GiB) — core, mimir
- GPU node groups (`gpu-l40s-a`, NVIDIA L40S, preemptible) — inference
- Cluster Autoscaler managed by Nebius

## Node Groups

All providers use the same config schema in each cluster's `config.yaml`:

| Cluster | Group | Platform / Type | Size | Purpose |
|---|---|---|---|---|
| `eks-alpha` (AWS) | `core` | `t2.medium` | 1–2 | System workloads |
| `eks-alpha` (AWS) | `mimir` | `t3.large` | 1–3 | Metrics (Mimir, Grafana) |
| `eks-alpha` (AWS) | `logging` | `t3.large` | 1–3 | Logging (ELK) |
| `eks-alpha` (AWS) | `g4-inference` | `g4dn.xlarge` spot | 0–3 | GPU inference (T4) |
| `eks-alpha` (AWS) | `g4-inference-32b` | `g4dn.12xlarge` spot + EFA | 2 | 32B model (4×T4, pipeline-parallel) |
| `scw-alpha` (Scaleway) | `core` | `DEV1-M` | 1–3 | System workloads |
| `scw-alpha` (Scaleway) | `mimir` | `DEV1-L` | 1–3 | Metrics |
| `scw-alpha` (Scaleway) | `logging` | `DEV1-L` | 1–3 | Logging |
| `nebius-alpha` (Nebius) | `core` | `cpu-d3` 16vcpu-64gb | 1–2 | System workloads |
| `nebius-alpha` (Nebius) | `mimir` | `cpu-d3` 16vcpu-64gb | 1–3 | Metrics |
| `nebius-alpha` (Nebius) | `gpu-inference` | `gpu-l40s-a` 8×L40S | 0–2 | GPU inference (preemptible) |

## Extensions (all providers)

Extensions are managed by Flux using a base/overlay Kustomize pattern under `iac-modules/extensions/`:

- **Cilium** — CNI with provider-specific routing overlays
- **cert-manager** — TLS certificate management
- **kgateway** — Kubernetes Gateway API
- **Cluster Autoscaler** — node scaling
- **Mimir + Grafana** — metrics storage and dashboards
- **ELK** — log aggregation
- **vLLM** — GPU inference serving (AWS only)

## Repository Layout

```
clusters/
  eks-alpha/       # AWS EKS workload cluster
  scw-alpha/       # Scaleway CAPS workload cluster
  scw-mgmt-alpha/  # Scaleway CAPS management cluster
  nebius-alpha/    # Nebius MK8s workload cluster

iac-modules/
  cluster-infra/
    v1.36-v1/      # AWS EKS provisioning module
    caps-v1/       # Scaleway CAPS provisioning module
    nebius-mk8s-v1/ # Nebius MK8s provisioning module
  extensions/      # Helm/Flux extension definitions (base + provider overlays)

config/            # SOPS-encrypted configuration values
docs/              # Provider-specific setup guides
```

## Quick Start

### Prerequisites

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Additional per provider:
- **AWS:** AWS CLI configured with EKS permissions
- **Scaleway:** `kind`, `clusterctl`, `sops`, `age`
- **Nebius:** Nebius service account credentials (see below)

---

### AWS EKS

```bash
source venv/bin/activate
aws configure
cd clusters/eks-alpha/infra
pulumi stack init eks-alpha
pulumi up
```

See [docs/eks.md](docs/eks.md) for the full setup guide.

---

### Scaleway CAPS

```bash
source venv/bin/activate
cd clusters/scw-alpha/infra
pulumi stack init scw-alpha
pulumi config set --secret scw_project_id <id>
pulumi config set --secret scw_access_key <key>
pulumi config set --secret scw_secret_key <secret>
pulumi up
```

See [docs/scaleway.md](docs/scaleway.md) for the full setup guide.

---

### Nebius MK8s

**1. Install the Nebius Pulumi SDK**

```bash
source venv/bin/activate
pulumi plugin install resource terraform-provider 1.2.1
pip install pulumi-nebius
```

**2. Set credentials**

Obtain a service account key from the Nebius console (IAM → Service accounts → Keys):

```bash
cd clusters/nebius-alpha/infra
pulumi stack init prod
pulumi config set nebius:account_id <service-account-id>
pulumi config set nebius:public_key_id <public-key-id>
pulumi config set nebius:private_key_file /path/to/private.pem
```

**3. Set project ID**

Edit `clusters/nebius-alpha/config.yaml` and fill in `project_id` from the Nebius console.

**4. Deploy**

```bash
pulumi up
```

**Scheduling workloads on dedicated nodes** — nodes carry taints so pods must include matching tolerations:

```yaml
# Example: schedule on mimir nodes
tolerations:
  - key: node-type
    value: mimir
    effect: NoSchedule
nodeSelector:
  node-type: mimir
```

**Upgrading the Nebius SDK** (after a new provider version is published):

```bash
pip install --upgrade pulumi-nebius
```

See [docs/nebius.md](docs/nebius.md) for the full setup guide.
