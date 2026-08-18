# nebius-alpha

Nebius MK8s cluster — GPU inference, including multi-node NVLink serving (Kimi K3, SGLang, TP=32/EP=32 across 4×8×H100 SXM).

This is a quick reference for provisioning *this specific cluster*. For the full walkthrough (screenshots of the Nebius console, how to generate a service-account key, etc.) see [docs/nebius.md](../../docs/nebius.md).

## What's here

```
clusters/nebius-alpha/
  config.yaml        # cluster name, project ID, k8s version, node_groups, nvlink_groups, dns
  infra/              # Pulumi program — provisions the cluster + bootstraps Flux
  extensions/         # Flux GitOps tree — everything after bootstrap is reconciled from here
```

Provisioning is two-phase: Pulumi creates the network, MK8s control plane, and node groups, then bootstraps Flux pointed at `extensions/` — from there, Flux reconciles everything else (GPU operator, LWS controller, cert-manager, kgateway, inference workloads) directly from git. Once Flux has reconciled the kgateway `Gateway`, Pulumi waits for its LoadBalancer to get an external IP and upserts the Cloudflare A record (see [DNS](#dns) below) — all within the same `pulumi up`.

## Prerequisites

```bash
cd <repo-root>
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

You'll also need:
- A Nebius service account key (`account_id`, `public_key_id`, private key PEM) — Nebius console → IAM → Service accounts.
- `sops` + `age`, and the age **private** key for this repo's `.sops.yaml` recipient, available locally as `SOPS_AGE_KEY_FILE`. Pulumi uses it for two things during `pulumi up`: decrypting `config/config.enc.yaml` (to build the Flux git-auth Secret) and populating the in-cluster `sops-age` Secret Flux itself uses to decrypt `*.enc.yaml` files under `extensions/` at reconcile time.

```bash
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
```

## Deploy

```bash
cd clusters/nebius-alpha/infra
pulumi login
pulumi stack ls                 # confirm which stack to use — check before assuming a name
pulumi stack select <stack>      # or: pulumi stack init <stack> for a fresh one
```

If setting up credentials fresh:
```bash
pulumi config set --secret nebius:account_id    <service-account-id>
pulumi config set --secret nebius:public_key_id <public-key-id>
pulumi config set --secret nebius:private_key_file /path/to/key.pem
```

`project_id` lives in `../config.yaml`, not Pulumi stack config — set it there (Nebius console → IAM → Projects).

```bash
pulumi preview   # inspect the plan first — this cluster provisions real GPU spend
pulumi up
```

Runs in sequence: VPC network + subnet → MK8s control plane (public endpoint) → node groups → Flux bootstrap. ~10–15 minutes for the control plane and CPU node groups; GPU node groups (`gpu-inference`, `gpu-nvlink`) can take longer, especially `gpu-nvlink` which also waits on its NVLink fabric (`nvlink_groups.nvlink-h100`) to come up first.

## Node groups (current `config.yaml`)

| Group | Platform / preset | Size | Purpose |
|---|---|---|---|
| `default`, `core` | `cpu-d3` 16vcpu-64gb | 1–2 | System workloads |
| `mimir` | `cpu-d3` 16vcpu-64gb | 1–3 | Metrics (tainted `node-type=mimir`) |
| `gpu-inference` | `gpu-l40s-d` 1×L40S, preemptible | 1–4 | Single-GPU inference |
| `gpu-nvlink` | `gpu-h100-sxm` 8×H100 SXM/node | **4 (fixed)** | Multi-node NVLink serving — Kimi K3 |

`gpu-nvlink` is fixed at exactly 4 nodes (`min_size == max_size == desired_size`), not autoscaled — the count is architectural, not load-driven. It's sized specifically for Kimi K3's MXFP4 checkpoint (~1.6TB): 3 nodes fit the weights but leave too little KV cache headroom, 4 is the practical minimum. See [docs/multi-node-inference-options.md](../../docs/multi-node-inference-options.md) for the sizing math, and [docs/nvlink-multinode-serving.md](../../docs/nvlink-multinode-serving.md) for how NVLink multi-node serving works generally.

Each `gpu-nvlink` node also joins the `nvlink-h100` `NvlInstanceGroup` (`nvlink_groups` in `config.yaml`) — Nebius provisions that NVLink Switch fabric before the nodes join the cluster, so all 32 H100s appear as one flat NVLink domain.

Scheduling onto a tainted node group requires a matching toleration + nodeSelector, e.g. for `gpu-nvlink`:
```yaml
nodeSelector:
  node-type: gpu-nvlink
tolerations:
  - key: nvidia.com/gpu
    operator: Exists
    effect: NoSchedule
  - key: workload
    operator: Equal
    value: inference-nvlink
    effect: NoSchedule
```

## What Flux reconciles after bootstrap

Everything under `extensions/`, as a chain of Flux `Kustomization` objects with explicit `dependsOn` ordering:

```
crds  ─┬─→ infra ─→ system ─→ crs
       └─→ llm-infra ─┐
                       └─→ llm-workloads
```

| Layer | Contains | Notes |
|---|---|---|
| `crds` | cert-manager, kgateway, and nvidia-gpu-operator CRDs | Applied first, everything else depends on it |
| `infra` | cert-manager, local-path-provisioner | Core cluster plumbing only |
| `llm-infra` | nvidia-gpu-operator (DRA driver) | Split out from `infra` deliberately — GPU driver install can take a long time on cold node scale-up, and nothing in `infra`/`system`/`crs` needs it, so it's not allowed to block them (`dependsOn: crds` only, no `wait: true`) |
| `system` | Flux itself, Grafana, kgateway, Mimir, OTel collector, Tempo, node-lifecycle-tracer | `dependsOn: infra` |
| `crs` | cert-manager issuers/certs, kgateway routes | `dependsOn: system` |
| `llm-workloads` | single-GPU vLLM (Kimi K3, LeaderWorkerSet + SGLang, is disabled — see `llm-workloads/kustomization.yaml`) | `dependsOn: crds, llm-infra` — needs the DRA driver actually present before its pods can be admitted. Re-enabling Kimi also requires restoring the LWS controller/CRDs, removed from `llm-infra`/`crds` |

## Access the cluster

Requires the Nebius CLI installed and a profile configured — see
[docs/nebius.md](../../docs/nebius.md#2-nebius-cli) if `nebius` isn't on
your `PATH` yet.

```bash
nebius mk8s cluster get-credentials --id <cluster-id> --external
# or:
pulumi stack output cluster_endpoint

kubectl get nodes
kubectl get pods -n vllm          # vLLM workloads
```

## DNS

Managed by Pulumi as part of `pulumi up` (see `dns.py` in the `nebius-mk8s-vX.Y-v1` module): after Flux bootstraps, it waits for the kgateway `main-gateway` LoadBalancer Service to get an external IP, then upserts a Cloudflare A record for `dns.name` under `dns.zone` (set in `config.yaml`). Nebius DNS v1 only supports VPC-private zones via API, so public DNS is managed in Cloudflare directly.

Requires a Cloudflare API token (Zone:DNS:Edit on the zone) at `.cf.api-key` in `config/config.enc.yaml` — add it with `sops config/config.enc.yaml`.

## Teardown

```bash
cd clusters/nebius-alpha/infra
pulumi destroy
```

Removes the MK8s cluster, node groups, subnet, and VPC network. Persistent volumes and load balancers created by in-cluster controllers (kgateway, CSI-provisioned PVs) may need manual cleanup first — `pulumi destroy` doesn't reach into resources Flux/Kubernetes created independently.

## Kimi K3 specifically

Before deploying, `iac-modules/extensions/kimi/vk3-v1/` needs its HF token secret encrypted and staged correctly — see `clusters/nebius-alpha/extensions/llm-workloads/kimi/secret-hf-token.enc.yaml`. If it's not yet re-encrypted with the current `.sops.yaml` `encrypted_regex` scope, `kustomize build`/Flux will fail on it:

```bash
sops -d -i clusters/nebius-alpha/extensions/llm-workloads/kimi/secret-hf-token.enc.yaml
sops -e -i clusters/nebius-alpha/extensions/llm-workloads/kimi/secret-hf-token.enc.yaml
```

Known open items on the Kimi K3 deployment itself (not blocking, but worth knowing before relying on it):
- `hf-cache` is `emptyDir` — every pod restart re-downloads the ~1.6TB checkpoint on all 4 nodes.
- `--context-length 32768` is conservative; there's headroom to raise it (see the KV-cache sizing discussion in `docs/multi-node-inference-options.md`).
- No load testing has been done — concurrency/throughput numbers anywhere in this repo's docs are estimates, not measurements.
