# AWS EKS Cluster Setup

This document covers provisioning the `prod` EKS cluster using Pulumi.

## Prerequisites

| Tool | Purpose |
|---|---|
| `pulumi` | Infrastructure provisioning |
| `aws` CLI | AWS authentication |
| `kubectl` | Kubernetes CLI |
| `sops` + `age` | Secret decryption |
| `python3` | Pulumi runtime |

```bash
# Verify all tools are available
for cmd in pulumi aws kubectl sops age; do
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
```

## 2. AWS credentials

```bash
aws configure
# or use environment variables:
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=eu-west-2

# Verify access
aws sts get-caller-identity
```

The IAM user or role needs permissions to manage VPC, EKS, EC2, IAM, and Auto Scaling resources.

## 3. Pulumi login & stack

```bash
pulumi login          # or: pulumi login --local for local state
cd clusters/prod/infra
pulumi stack init prod   # skip if stack already exists
```

## 4. Stack configuration

All settings have defaults defined in `clusters/prod/infra/Pulumi.yaml`. Override any that differ for your environment:

```bash
# Required only if defaults don't match your environment
pulumi config set eks-pulumi:cluster_name      infra-cluster
pulumi config set eks-pulumi:cluster_version   "1.33"
pulumi config set eks-pulumi:vpc_cidr          "10.0.0.0/16"
pulumi config set eks-pulumi:availability_zones "eu-west-2a,eu-west-2c"

# IAM user ARN(s) to grant cluster admin (comma-separated)
pulumi config set eks-pulumi:cluster_admin_user_arns "arn:aws:iam::<account-id>:user/<username>"
```

## 5. SOPS age key

Flux uses SOPS to decrypt `config/config.enc.yaml`, which contains the GitHub token for the `flux-system` GitRepository.

```bash
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt

# Verify the key exists
cat $SOPS_AGE_KEY_FILE | head -1
```

The age public key must be in `.sops.yaml` at the repo root. If setting up fresh, generate a key, add the public key to `.sops.yaml`, then re-encrypt `config/config.enc.yaml`.

## 6. Review node groups

Node group definitions are in `clusters/prod/config.yaml`. Verify instance types, sizes, and AMI IDs match your target region before deploying:

```yaml
node_groups:
  core:
    instance_types: ["t2.medium"]
    desired_size: 1
    ami_id: "ami-060bb37b943ff8d8e"   # eu-west-2 custom AMI — update for other regions
  mimir:
    instance_types: ["t3.large"]
    ...
```

## 7. Deploy

```bash
cd clusters/prod/infra
pulumi preview   # inspect the plan first
pulumi up
```

Pulumi runs the following steps in sequence:

1. Creates VPC, public/private subnets, security groups across `eu-west-2a` and `eu-west-2c`
2. Creates the EKS cluster (Kubernetes 1.33) with IAM Access Entries for auth
3. Sets up AWS LBC IRSA and Karpenter Pod Identity
4. Installs **Cilium** CNI and **CoreDNS** via Helm
5. Creates managed **node groups** (core, mimir, logging) and waits for them to be Ready
6. Bootstraps **Flux** — GitOps takes over from here
7. Attaches Auto Scaling Groups to NLB target groups

Total time: ~15–20 minutes.

## 8. Access the cluster

```bash
# Update local kubeconfig
aws eks update-kubeconfig --name infra-cluster --region eu-west-2

# Verify nodes
kubectl get nodes

# Check Flux is reconciling
kubectl -n flux-system get kustomizations
kubectl -n flux-system get gitrepositories
```

## What Flux manages after bootstrap

Once Flux is running it reconciles everything under `clusters/prod/extensions/`:

| Component | Layer |
|---|---|
| Cilium, CoreDNS, cert-manager, Karpenter, local-path-provisioner | `infra` |
| kgateway, Grafana, Mimir, ELK, Flux itself | `system` |
| cert-manager issuers/certs, kgateway routes, Karpenter NodePools | `crs` |

Extension Helm values come from `iac-modules/extensions/`, with EKS-specific overlays under `eks/` where applicable (e.g. `cilium/v1.18.3-v1/eks/`).

## Teardown

```bash
cd clusters/prod/infra
pulumi destroy
```

> **Note:** `pulumi destroy` will delete the VPC, EKS cluster, and all node groups. Persistent volumes and load balancers created by in-cluster controllers (e.g. Flux, Karpenter) may need manual cleanup first.
