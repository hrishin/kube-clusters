# Multi-Node Inference Orchestration — Options

This document compares the available approaches for running multi-node GPU inference on Kubernetes, focusing on the NVLink cluster in `nebius-alpha`. There are two independent axes: the **Kubernetes scheduling primitive** (how pods are grouped and co-scheduled) and the **serving engine** (how the model is loaded and requests are processed).

```
Scheduling primitive      Serving engine
───────────────────       ──────────────
StatefulSet          ×    torchrun + vLLM       (original design)
LeaderWorkerSet      ×    torchrun + vLLM       (recommended)
LeaderWorkerSet      ×    NVIDIA Dynamo         (production scale)
```

---

## Axis 1 — Scheduling primitive

### StatefulSet (original)

StatefulSets give each pod a stable ordinal and DNS name, which we used to derive the torchrun rank (`${HOSTNAME##*-}`). It works but has structural problems for multi-node inference:

| Problem | Consequence |
|---|---|
| No co-scheduling guarantee | Pod 0 can start and load 72 GB of weights while pod 1 is still pending. The NCCL rendezvous on port 29500 will block indefinitely until pod 1 arrives, tying up the node. |
| Shared pod spec | Leader and workers must use the same container spec: same probes, same ports, same entrypoint. We worked around the readiness probe problem with a placeholder HTTP server on workers. |
| Rolling updates are per-pod | A `kubectl rollout restart` updates pod 0 then pod 1 separately. Between those two events the cluster has a split-brain: new leader binary paired with an old worker binary sharing a NCCL communicator. |
| Horizontal scaling is manual | Serving two independent TP groups requires two separate StatefulSet manifests, two Services, manual HTTPRoute config. |

### LeaderWorkerSet — LWS (recommended)

LeaderWorkerSet (`kubernetes-sigs/lws`) is a CRD that models exactly this topology: one or more groups, each with a designated leader pod and N worker pods.

**What LWS gives you out of the box:**

```
LeaderWorkerSet (replicas=1, size=2)
  Group 0
    vllm-nvlink-0   → leader  (LWS_WORKER_INDEX=0, runs HTTP API)
    vllm-nvlink-0-1 → worker  (LWS_WORKER_INDEX=1, runs forward-pass loop)
```

| Feature | How it works |
|---|---|
| **Co-scheduling** | If worker pods cannot be placed, the leader is not scheduled either. No partial groups. |
| **`LWS_LEADER_ADDRESS`** | Injected into every pod as an env var — the stable DNS name of the leader. torchrun `--master-addr` is just `${LWS_LEADER_ADDRESS}`. No headless service, no hostname parsing. |
| **`LWS_WORKER_INDEX`** | Exposed via downward API from `leaderworkerset.sigs.k8s.io/worker-index` annotation. torchrun `--node-rank` is just `${LWS_WORKER_INDEX}`. |
| **Separate pod specs** | `leaderTemplate` and `workerTemplate` are independent. Leader gets an HTTP readiness probe; workers get a process-level exec probe. No placeholder health server needed. |
| **`RecreateGroupOnPodRestart`** | Any pod crash restarts the entire group. Correct for multi-node TP: a worker crash invalidates the NCCL communicator, so the leader cannot serve anyway. |
| **Horizontal scaling** | `replicas: 2` creates two independent leader+worker groups. kgateway load-balances across both leaders via `role: leader` Service selector. |

**Horizontal scaling with LWS:**

```yaml
spec:
  replicas: 2    # two independent TP groups
  # LWS creates:
  #   Group 0: vllm-nvlink-0 (leader) + vllm-nvlink-0-1 (worker)
  #   Group 1: vllm-nvlink-1 (leader) + vllm-nvlink-1-1 (worker)
```

The Service selector `role: leader` automatically includes both leaders. No second StatefulSet, no second Service, no manual HTTPRoute changes.

**Installing LWS:**

Installed via the official Helm chart (`oci://registry.k8s.io/lws/charts/lws`), through a Flux `HelmRepository` + `HelmRelease` in the infra layer. CRDs are applied separately through the `crds` layer — the chart's README notes that CRD schema changes don't reach the cluster via `helm upgrade`, so they're managed outside the Helm release lifecycle (same pattern used for cert-manager):

```bash
# Verify controller is running:
kubectl get pods -n lws-system
```

The extension `iac-modules/extensions/lws/v0.9.0-v1/` installs the LWS controller via Flux `HelmRelease`. Its CRDs live in `iac-modules/extensions/crds/lws/v0.9.0/`, wired into `clusters/nebius-alpha/extensions/crds/`.

---

## Axis 2 — Serving engine

### torchrun + vLLM

All pods run the same `torchrun` command. torchrun handles:
1. Spawning 8 CUDA processes per node
2. `torch.distributed` rendezvous (connecting all processes via port 29500)
3. NCCL communicator group setup

After rendezvous, vLLM's scheduler on rank 0 dispatches forward passes to all 16 ranks via NCCL. All communication (all-reduce, KV cache) is in-process or via NCCL over NVLink. No middleware.

**Strengths:** Simple, battle-tested, no extra infrastructure.

**Limitation:** Prefill and decode run on the same GPU pool, competing for the same HBM. For long-context workloads, prefill (compute-intensive) and decode (memory-bandwidth-intensive) have very different GPU utilisation profiles — running them together leaves both phases underutilised.

### NVIDIA Dynamo

NVIDIA Dynamo (github.com/ai-dynamo/dynamo) is a distributed inference framework that disaggregates prefill and decode into separate worker pools.

**Core idea — disaggregated prefill/decode:**

```
Request → Router
            │
            ├─→ Prefill workers (compute-bound)
            │     Process the input prompt in full
            │     Generate KV cache for all prompt tokens
            │     Transfer KV cache → Decode workers via NIXL
            │
            └─→ Decode workers (memory-bandwidth-bound)
                  Receive pre-computed KV cache
                  Generate tokens one at a time
                  Stream response back to client
```

**Why this helps:**

| Phase | Bottleneck | Optimal hardware |
|---|---|---|
| Prefill (prompt processing) | Compute (FLOPS) | High-FLOP GPUs, large batch |
| Decode (token generation) | Memory bandwidth (HBM BW) | High-HBM-BW GPUs, many concurrent sequences |

Running them on the same GPU means the hardware is only optimally utilised for one phase at a time. Disaggregation lets each phase run on hardware matched to its bottleneck, or on the same hardware but with separate scheduling queues that prevent one phase from starving the other.

**KV cache transfer on NVLink:**

Between prefill and decode workers on the same NVLink fabric, Dynamo uses NIXL (NVIDIA's interconnect abstraction library) to transfer KV cache over NVLink rather than over the host network. KV cache transfer at NVLink bandwidth (~1 TB/s) vs TCP (~10 Gb/s) is a 100× improvement — for large contexts this makes disaggregation viable where it would otherwise be impractical.

**Dynamo components on Kubernetes:**

```
┌─────────────────────────────────────────────────────────────────┐
│  Dynamo serving graph                                            │
│                                                                  │
│  Frontend Deployment          (OpenAI-compatible, stateless)     │
│       │                                                          │
│       ▼ NATS                                                     │
│  Router Deployment            (routes prefill vs decode)         │
│       │               │                                          │
│       ▼               ▼                                          │
│  Prefill LWS       Decode LWS                                    │
│  (TP=8 per group)  (TP=16 per group)                             │
│  compute-heavy     memory-bandwidth-heavy                        │
│       │               │                                          │
│       └───────────────┘                                          │
│        NIXL KV cache transfer (NVLink / RDMA)                   │
└─────────────────────────────────────────────────────────────────┘
```

**Dynamo deployment prerequisites:**

- NATS message broker (Helm chart: `nats/nats`)
- Dynamo operator (Helm chart: `dynamo-charts/dynamo-operator`)
- NIXL library on nodes (NVIDIA provides as a DaemonSet or baked into node image)

**Minimal Dynamo deployment spec:**

```yaml
apiVersion: nvidia.com/v1alpha1
kind: DynamoGraphDeployment
metadata:
  name: vllm-nvlink-dynamo
spec:
  services:
    Frontend:
      replicas: 1
    Router:
      replicas: 1
    VllmWorker:            # disaggregated mode
      prefill:
        replicas: 1
        numGpus: 8
        envs:
        - name: MODEL_PATH
          value: Qwen/Qwen2.5-72B-Instruct
        - name: TENSOR_PARALLEL_SIZE
          value: "8"
        - name: DISAGGREGATED_ROLE
          value: prefill
      decode:
        replicas: 2        # more decode replicas to sustain throughput
        numGpus: 8
        envs:
        - name: MODEL_PATH
          value: Qwen/Qwen2.5-72B-Instruct
        - name: TENSOR_PARALLEL_SIZE
          value: "8"
        - name: DISAGGREGATED_ROLE
          value: decode
```

---

## The weight-sharding constraint and how Dynamo handles it

This section answers a specific design question: *if the model weights don't fit on a single node, how does disaggregated serving work? Do the prefill and decode pools share the weight shards between them?*

### The constraint

A model that doesn't fit on one node's combined GPU HBM must be sharded across multiple nodes. This sharding is driven purely by memory capacity — it is not optional. For Kimi K3 in MXFP4 (~1.6 TB) on H100 SXM (80 GB per GPU):

```
1 node  × 8 GPUs × 80 GB = 640 GB   — 1.6 TB doesn't fit
2 nodes × 8 GPUs × 80 GB = 1,280 GB — still doesn't fit (1,280 < 1,600)
3 nodes × 8 GPUs × 80 GB = 1,920 GB — fits, ~300 GB left for KV cache
```

Every forward pass — whether it is processing a prompt (prefill) or generating a token (decode) — requires access to all model weights. You cannot run a half-forward-pass using only half the weights.

### What Dynamo disaggregates

Dynamo disaggregates **requests into two phases**, not **weights into two pools**.

```
What gets disaggregated:  REQUESTS (prefill phase vs decode phase)
What does NOT get split:  WEIGHTS  (each pool holds the full model independently)

                      ┌──────────────────────────────────────┐
                      │         PREFILL POOL                  │
                      │  (compute-bound: processes prompt)    │
                      │                                       │
                      │  Node 0: GPU 0–7  TP ranks 0–7       │
                      │  Node 1: GPU 0–7  TP ranks 8–15      │
                      │  Node 2: GPU 0–7  TP ranks 16–23     │
                      │                                       │
                      │  Full model sharded across 24 GPUs   │
                      │  Each GPU: 1/24 of every weight matrix│
                      └───────────────┬──────────────────────┘
                                      │  KV cache transfer
                                      │  (NIXL over NVLink/RDMA)
                                      ▼
                      ┌──────────────────────────────────────┐
                      │         DECODE POOL                   │
                      │  (memory-bandwidth-bound: gen tokens) │
                      │                                       │
                      │  Node 3: GPU 0–7  TP ranks 0–7       │
                      │  Node 4: GPU 0–7  TP ranks 8–15      │
                      │  Node 5: GPU 0–7  TP ranks 16–23     │
                      │                                       │
                      │  Full model sharded across 24 GPUs   │
                      │  Each GPU: 1/24 of every weight matrix│
                      └──────────────────────────────────────┘
```

Both pools hold the **entire model independently**. The prefill pool runs a complete forward pass on the prompt using TP=24. The decode pool runs complete forward passes for token generation using TP=24. They do not share weight memory. The only thing that flows from prefill to decode is the **KV cache** — the computed key and value vectors for the prompt tokens.

### Why the KV cache is what flows (not the weights)

During prefill, the model computes three vectors per token per layer: Q (query), K (key), V (value). Q is used immediately and discarded. K and V — the KV cache — represent what the model "remembers" about each prompt token. In decode, every new token must attend to all previous K and V vectors. These are the only tensors that need to move.

```
Prefill pool computes (for a 10,000 token prompt, 80 layers, K3 approximate):
  KV cache = 80 layers × (K + V) per token × 10,000 tokens
           ≈ 80 × 2 × head_dim × kv_heads × dtype_size × 10,000
           ≈ 80 × 2 × 128 × 128 × 1 byte × 10,000
           ≈ 26 GB

This 26 GB is what NIXL transfers from the prefill pool to the decode pool.
The ~1.6 TB of weight shards stay on each pool's own GPUs — they never move.
```

### Hardware consequence: weights are duplicated

Because both pools hold the full model independently, disaggregated serving **doubles the weight memory requirement**:

```
Non-disaggregated (torchrun):
  1 pool of N GPUs holds the model once
  GPU count driven by: weight_size + kv_cache_for_max_concurrency

Disaggregated (Dynamo):
  2 pools of N GPUs each hold the model independently
  GPU count driven by: 2 × weight_size + separate kv_cache per pool
```

For Kimi K3 on H100 SXM (80 GB), minimum GPU counts:

```
Non-disaggregated:
  Weights: 1,600 GB ÷ 80 GB = 20 GPUs minimum → 3 nodes
  + KV cache → 4 nodes practical

Disaggregated (1 prefill pool + 1 decode pool):
  Prefill pool:  3–4 nodes (same constraint as non-disaggregated)
  Decode pool:   3–4 nodes (same constraint, independent copy of weights)
  Total:         6–8 nodes minimum for a single serving unit
```

For H200 (141 GB per GPU), 2 nodes per pool → 4 nodes total for one disaggregated unit. This is Moonshot's recommended 2-node H200 experimentation config — but note that is for non-disaggregated. Disaggregated K3 on H200 needs 4 nodes.

### Within each pool: how weights are sharded

Inside a pool, weight sharding is exactly the same as in the non-disaggregated torchrun design: tensor parallelism splits each weight matrix across the pool's GPUs. For a 3-node prefill pool (24 GPUs, TP=24):

```
MLP W_gate [hidden=7168 → intermediate=18432]:
  GPU 0:   columns     0 –   767  (18432 ÷ 24)
  GPU 1:   columns   768 –  1535
  ...
  GPU 23:  columns 17664 – 18431

All-reduce after MLP W_down: NCCL over NVLink within the pool.
No cross-pool communication during the forward pass.
```

Each pool uses its own NVLink fabric for intra-pool TP all-reduces. The prefill and decode pools are on **separate NVLink fabrics** (separate NvlInstanceGroups on Nebius), and the only cross-pool transfer is the KV cache via NIXL.

### The KV cache transfer in detail

NIXL (NVIDIA Interconnect Library) abstracts the transport for KV cache transfer. On Nebius:

```
If prefill and decode share a NVLink fabric:
  NIXL uses NVLink → ~1.8 TB/s, microseconds for typical KV sizes

If prefill and decode are on separate NVLink fabrics (different NvlInstanceGroups):
  NIXL falls back to InfiniBand (NDR, 400 Gb/s) or TCP
  NDR IB for 26 GB KV (10K token prompt): 26 GB ÷ 50 GB/s ≈ 520 ms
  NVLink for 26 GB KV: 26 GB ÷ 1800 GB/s ≈ 14 ms
```

This is why Dynamo's disaggregation benefit depends heavily on the interconnect between the two pools. On a shared NVLink fabric (both pools on the same rack), KV transfer is fast enough to be a minor overhead. On separate racks with InfiniBand, the transfer cost for long-context prompts can negate the disaggregation benefit.

### When is disaggregation worth the GPU cost?

Disaggregation doubles the weight memory footprint. It is worth it when:

| Condition | Explanation |
|---|---|
| Prefill and decode saturate independently | A single pool can only do one at a time — prefill blocks decode and vice versa. At high QPS, this creates head-of-line blocking. |
| Prefill duration >> decode step duration | For long prompts (>4K tokens), prefill can take seconds. Without disaggregation, decode for other requests is blocked during that prefill. |
| Hardware can be specialised | Prefill pool can use fewer, higher-FLOP GPUs; decode pool can use more GPUs with higher HBM bandwidth. |
| KV transfer cost is low | Requires fast interconnect between pools (NVLink on same fabric, or NDR IB at minimum). |

For the current 2-node NVLink cluster (16 GPUs, 1,280 GB HBM total): **disaggregation is not viable for K3**. The cluster is below the minimum to host even one pool's weight copy for K3. It is viable for 72B (144 GB weights, fits easily on 2 nodes) if the request pattern shows the prefill-blocking-decode problem.

---

## Option comparison

| | StatefulSet + torchrun | **LWS + torchrun** | LWS + Dynamo |
|---|---|---|---|
| **Scheduling guarantee** | None — partial groups possible | Co-scheduled, atomic group | Co-scheduled, per-role |
| **Role-aware probe** | No — placeholder workaround | Yes — separate leader/worker specs | Yes |
| **Worker rank injection** | Hostname parsing | `LWS_LEADER_ADDRESS` + `LWS_WORKER_INDEX` | Dynamo runtime |
| **Group restart on pod crash** | Manual | `RecreateGroupOnPodRestart` | Operator-managed |
| **Horizontal scaling** | Manual second StatefulSet | `replicas: N` on one resource | `replicas` per service |
| **Prefill/decode disaggregation** | No | No | Yes |
| **KV cache transfer** | N/A (same process) | N/A (same process) | NIXL over NVLink |
| **Extra infrastructure** | None | LWS controller | NATS + Dynamo operator + NIXL |
| **Operational maturity** | High | High | Medium (newer project) |
| **Best for** | Reference / experimentation | Production serving | High-throughput long-context |

---

## Recommendation

**Use LWS + torchrun for all new deployments.** It fixes every structural problem of the StatefulSet approach with no additional serving-side complexity. The manifest is at `iac-modules/extensions/vllm-nvlink-lws/v0.9-v1/`.

**Evaluate Dynamo when:**
- Serving long-context requests (>16K tokens) where prefill latency dominates
- Decode concurrency is high enough that a dedicated decode pool is justified (decode workers serving many sequences simultaneously while prefill workers process new requests in parallel)
- The NVLink fabric is available for KV cache transfer (this cluster satisfies that condition)
- The team is ready to operate NATS and the Dynamo operator

**For Kimi K3 at scale:** Dynamo disaggregated serving is the right architecture. K3's 1M-token context means prefill can take minutes for very long inputs — a dedicated prefill pool prevents those requests from blocking decode throughput. NVLink KV transfer makes the disaggregation practical at K3's scale.

---

## Files

```
iac-modules/extensions/lws/v0.9.0-v1/
  repo.yaml                       # HelmRepository, oci://registry.k8s.io/lws/charts/lws
  release.yaml                    # HelmRelease (CRDs skipped, applied via crds layer)
  kustomization.yaml

iac-modules/extensions/crds/lws/v0.9.0/
  leaderworkerset.x-k8s.io_leaderworkersets.yaml
  disaggregatedset.x-k8s.io_disaggregatedsets.yaml
  kustomization.yaml

iac-modules/extensions/vllm-nvlink-lws/v0.9-v1/
  leadeworkerset.yaml             # LWS with separate leader/worker specs
  service.yaml                    # routes to role: leader pods only
  resourceclaimtemplate.yaml      # DRA: 8 GPUs per pod
  httproute.yaml                  # inference-nvlink.nebius.internal
  kustomization.yaml
```

---

## Related

- [nvlink-multinode-serving.md](nvlink-multinode-serving.md) — NVLink architecture, weight sharding, load balancing
- [kimi-k3-self-hosted.md](kimi-k3-self-hosted.md) — K3 hardware sizing and deployment stages
