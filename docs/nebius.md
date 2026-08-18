# Nebius MK8s Cluster Setup

This document covers provisioning the `nebius-alpha` Nebius Managed Kubernetes cluster using Pulumi.

## Prerequisites

| Tool | Purpose |
|---|---|
| `pulumi` | Infrastructure provisioning |
| `kubectl` | Kubernetes CLI |
| `nebius` | Nebius CLI — cluster kubeconfig retrieval, ad-hoc resource inspection |
| `sops` + `age` | Secret decryption |
| `python3` | Pulumi runtime |

```bash
# Verify all tools are available
for cmd in pulumi kubectl nebius sops age; do
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
pulumi plugin install resource terraform-provider 1.2.1
```

## 2. Nebius CLI

Separate from the Pulumi provider's service-account auth below — this is
your personal, federated-login CLI, used for `nebius mk8s cluster
get-credentials` (step 9) and any ad-hoc `nebius ...` inspection. Full
reference: https://docs.nebius.com/cli/install

```bash
curl -sSL https://storage.eu-north1.nebius.cloud/cli/install.sh | bash
exec -l $SHELL   # reload PATH, or open a new terminal

nebius version   # verify
```

Create a profile against the project you'll be deploying into (same
`project_id` as step 5 below — Nebius console: **IAM → Projects**):

```bash
nebius profile create \
  --profile nebius-alpha \
  --endpoint api.nebius.cloud \
  --federation-endpoint auth.nebius.com \
  --parent-id <project-id>
```

This opens a browser tab for federated login at `auth.nebius.com`; once it
confirms "Login is successful", the CLI is authenticated and `nebius`
commands will use this profile.

## 3. Nebius service account credentials

Create a service account in the Nebius console under **IAM → Service accounts**, grant it the `editor` role on the project, then generate an authorized key:

**Console path:** IAM → Service accounts → *your SA* → Authorized keys → Create

This gives you:
- `account_id` — the service account ID (starts with `serviceaccount-`)
- `public_key_id` — the key ID
- A PEM private key file (download and store it locally, e.g. `~/.nebius/prod-sa.pem`)

## 4. Pulumi login & stack

```bash
pulumi login          # or: pulumi login --local for local state
cd clusters/nebius-alpha/infra
pulumi stack init nebius-alpha   # skip if stack already exists
```

## 5. Stack configuration

```bash
pulumi config set --secret nebius:account_id    <service-account-id>
pulumi config set --secret nebius:public_key_id <public-key-id>
pulumi config set --secret nebius:private_key_file /path/to/prod-sa.pem
```

Set the Nebius project ID in `clusters/nebius-alpha/config.yaml`:

```yaml
project_id: "project-<id>"   # from Nebius console: IAM → Projects
```

## 6. SOPS age key

Flux uses SOPS to decrypt `config/config.enc.yaml`, which contains the GitHub token for the `flux-system` GitRepository.

```bash
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt

# Verify the key exists
cat $SOPS_AGE_KEY_FILE | head -1
```

The age public key must be in `.sops.yaml` at the repo root. If setting up fresh, generate a key, add the public key to `.sops.yaml`, then re-encrypt `config/config.enc.yaml`.

## 7. Review node groups

Node group definitions are in `clusters/nebius-alpha/config.yaml`:

```yaml
node_groups:
  default:
    platform: cpu-d3
    preset: 16vcpu-64gb
    desired_size: 1
    min_size: 1
    max_size: 2
    preemptible: false

  core:
    platform: cpu-d3
    preset: 16vcpu-64gb
    # ...

  mimir:
    platform: cpu-d3
    preset: 16vcpu-64gb
    preemptible: false
    taints:
      - key: node-type
        value: mimir
        effect: NoSchedule
```

Scheduling pods on tainted nodes requires a matching toleration:

```yaml
tolerations:
  - key: node-type
    value: mimir
    effect: NoSchedule
nodeSelector:
  node-type: mimir
```

## 8. Deploy

```bash
cd clusters/nebius-alpha/infra
pulumi preview   # inspect the plan first
pulumi up
```

Pulumi runs the following steps in sequence:

1. Creates a VPC network and subnet in the Nebius project
2. Creates the MK8s cluster with a public control-plane endpoint
3. Creates node groups
4. Bootstraps **Flux** — GitOps takes over from here

Total time: ~10–15 minutes.

## 9. Access the cluster

```bash
# Fetch kubeconfig via the Nebius CLI
nebius mk8s cluster get-credentials --id <cluster-id> --external

# Or use the endpoint directly — get it from Pulumi outputs
pulumi stack output cluster_endpoint

# Verify nodes
kubectl get nodes
```

## What Flux manages after bootstrap

Once Flux is running it reconciles everything under `clusters/nebius-alpha/extensions/`:

| Component | Layer |
|---|---|
| cert-manager, local-path-provisioner, NVIDIA GPU operator | `infra` |
| Flux, Grafana, kgateway, Mimir, OTel collector, Tempo, node-lifecycle-tracer | `system` |
| cert-manager issuers/certs, kgateway routes | `crs` |
| NVIDIA GPU runtime configuration | `gpu` |

## Stack outputs

| Output | Description |
|---|---|
| `cluster_id` | MK8s cluster resource ID |
| `cluster_name` | Cluster name (`nebius-alpha`) |
| `cluster_endpoint` | Public API server URL |
| `network_id` | VPC network ID |
| `subnet_id` | Subnet ID |
| `node_group_ids` | Map of node group name → resource ID |

## Teardown

```bash
cd clusters/nebius-alpha/infra
pulumi destroy
```

> **Note:** `pulumi destroy` removes the MK8s cluster, node groups, subnet, and VPC network. Persistent volumes and load balancers created by in-cluster controllers may need manual cleanup first.
