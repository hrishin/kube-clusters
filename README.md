## Kubernetes Infrastructure

This repository contains Pulumi programs and GitOps configuration for provisioning and managing Kubernetes clusters on **AWS EKS** and **Scaleway** (via Cluster API Provider Scaleway — CAPS).

## Supported Providers

| Provider | Cluster | Module |
|---|---|---|
| AWS EKS | `clusters/prod`, `clusters/mgmt` | `iac-modules/cluster-infra/eks-*` |
| Scaleway CAPS | `clusters/prod-scw` | `iac-modules/cluster-infra/caps-v1` |

## What Gets Provisioned

**AWS EKS**
- VPC, subnets, and networking stack
- EKS cluster and managed node groups
- Flux bootstrapped from `clusters/prod/extensions`

**Scaleway CAPS**
- Local `kind` management cluster running CAPI + CAPS controllers
- Scaleway private network, control plane, and worker MachineDeployments
- Cilium CNI, CoreDNS, Flux bootstrapped from `clusters/prod-scw/extensions`
- Post-bootstrap: Scaleway CCM, CSI, Cluster Autoscaler via GitOps

## Extensions (both providers)

Extensions are managed by Flux using a base/overlay Kustomize pattern under `iac-modules/extensions/`:

- **Cilium** — CNI with provider-specific routing config (`eks/` or `scaleway/` overlay)
- **cert-manager** — TLS certificate management
- **kgateway** — Kubernetes Gateway API implementation
- **Cluster Autoscaler** — node scaling

## Repository Layout

```
clusters/
  mgmt/          # AWS EKS management cluster
  prod/          # AWS EKS workload cluster
  prod-scw/      # Scaleway CAPS workload cluster

iac-modules/
  cluster-infra/ # Pulumi cluster provisioning modules
  extensions/    # Helm/Flux extension definitions (base + provider overlays)

config/          # SOPS-encrypted configuration values
docs/            # Provider-specific setup guides
```

## Quick Start

### AWS EKS

```bash
source venv/bin/activate
./setup-pulumi.sh
pulumi -C clusters/prod/infra up
```

### Scaleway

See [docs/scaleway.md](docs/scaleway.md) for the full setup guide.

```bash
source venv/bin/activate
cd clusters/prod-scw/infra
pulumi config set --secret scw_project_id <id>
pulumi config set --secret scw_access_key <key>
pulumi config set --secret scw_secret_key <secret>
pulumi up
```

## Prerequisites

- `pulumi` CLI
- `python3` with dependencies: `pip install -r requirements.txt`
- **AWS:** configured AWS credentials with EKS permissions
- **Scaleway:** `kind`, `clusterctl`, `sops`, `age`
