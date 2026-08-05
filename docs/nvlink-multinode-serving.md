# Multi-Node NVLink Model Serving — Design

This document describes the architecture for running very large language models (70B+) across multiple GPU nodes connected by an NVLink Switch fabric on Nebius AI Cloud.

---

## Why NVLink multi-node?

Serving large models involves two unavoidable bottlenecks: **memory capacity** (do the weights fit?) and **interconnect bandwidth** (how fast can GPUs synchronize activations?).

Single-node H100 SXM gives 8 GPUs × 80 GB = 640 GB HBM — enough for 70B in FP16 but tight for 405B or larger. Scaling beyond one node traditionally means pipeline or tensor parallelism over InfiniBand or EFA, which are 15–20× slower than NVLink. That gap forces awkward scheduling choices (pipeline-parallel introduces pipeline bubbles and batching constraints; tensor-parallel over slow links creates all-reduce latency that dominates time-to-first-token).

NVLink Switch fabric eliminates the interconnect bottleneck by extending the NVLink domain across nodes:

| Transport | Bandwidth (per GPU) | Topology |
|---|---|---|
| NVLink4 (intra-node) | 900 GB/s bidirectional | 8-GPU all-to-all |
| NVLink Switch (multi-node, GB200) | 1.8 TB/s bidirectional | rack-scale all-to-all |
| InfiniBand HDR (HPC multi-node) | ~50 GB/s | switch-based |
| AWS EFA (multi-node) | ~50 GB/s per GPU | switch-based |

With NVLink Switch, two or more nodes can run **pure tensor parallelism** at the same bandwidth as intra-node NVLink. There is no need for pipeline parallelism, which means no pipeline bubbles, simpler scheduling, and lower latency.

---

## Hardware topology

```
Nebius GB200 NVLink Rack (NvlInstanceGroup, type=GB200)
┌────────────────────────────────────────────────────────┐
│                   NVLink Switch Fabric                  │
│         1.8 TB/s bidirectional, all-to-all             │
│  ┌──────────────────┐    ┌──────────────────┐          │
│  │     Node 0       │    │     Node 1       │          │
│  │  8 × H100 SXM    │    │  8 × H100 SXM    │          │
│  │  640 GB HBM3e    │    │  640 GB HBM3e    │          │
│  │                  │    │                  │          │
│  │ NVLink4 900 GB/s │    │ NVLink4 900 GB/s │          │
│  │  (intra-node)    │    │  (intra-node)    │          │
│  └──────────────────┘    └──────────────────┘          │
│                                                        │
│   Total: 16 × H100 SXM │ 1,280 GB HBM │ TP=16         │
└────────────────────────────────────────────────────────┘
```

The NVLink Switch makes all 16 GPUs appear as one flat all-reduce domain. NCCL discovers this topology automatically through CUDA topology files — no special environment variables are needed.

---

## Parallelism strategy

| Config | GPUs | TP | PP | Best for |
|---|---|---|---|---|
| Single-node L40S | 1 | 1 | 1 | 7B, 13B |
| Single-node H100 SXM | 8 | 8 | 1 | 70B FP8, 34B FP16 |
| Two-node NVLink (this design) | 16 | **16** | 1 | 70B FP16, 405B FP8 |
| Two-node InfiniBand / EFA | 16 | 4 | **4** | same models, higher latency |

**Tensor parallelism (TP)** splits each weight matrix column-wise across GPUs. Every attention and MLP layer does one all-reduce per forward pass. At NVLink bandwidth, this all-reduce takes microseconds — it is effectively free compared to the compute itself.

**Pipeline parallelism (PP)** splits layers across GPUs, which avoids the all-reduce entirely but introduces a pipeline bubble (idle time between micro-batches) and limits minimum latency to `(PP - 1) × layer_latency`. PP is the right choice over slow interconnects (IB/EFA); NVLink makes it unnecessary.

---

## How weights are loaded across two nodes

Each GPU holds a **shard** of the weights — not a full copy. This is the defining property of tensor parallelism and it is worth being precise about, because it determines memory footprint, loading time, and what must cross the NVLink fabric during inference.

### Weight distribution for Qwen2.5-72B FP16

```
Total model:  ~144 GB
TP rank:      0–15  (16 GPUs across 2 nodes)
Per GPU:      ~9 GB  (144 GB ÷ 16)

Node 0  (GPUs 0–7,   TP ranks 0–7 )  →  ~72 GB of weights
Node 1  (GPUs 8–15,  TP ranks 8–15)  →  ~72 GB of weights
```

Neither node holds a complete copy of the model. Each layer's weight matrices are sliced and each GPU retains only its slice.

### How each layer type is sharded

**MLP / Feed-Forward layers** (two weight matrices per layer, column-split then row-split):

```
W_gate / W_up  [hidden=8192 → intermediate=22016]
  GPU 0:  columns   0 –  1375   (1/16 of 22016)
  GPU 1:  columns 1376 –  2751
  ...
  GPU 15: columns 20640 – 22015

W_down  [intermediate=22016 → hidden=8192]
  GPU 0:  rows   0 –  1375   (1/16 of inputs)
  GPU 1:  rows 1376 –  2751
  ...

After W_down: all-reduce sum across all 16 GPUs → full activation [seq, 8192]
```

**Self-attention** (split by attention heads):

```
Qwen2.5-72B:  64 Q heads,  8 KV heads  (GQA)
TP=16:
  GPUs 0–1   handle Q heads 0–7,   KV head 0
  GPUs 2–3   handle Q heads 8–15,  KV head 1
  ...
  GPUs 14–15 handle Q heads 56–63, KV head 7

Q/K/V projection weights: each GPU holds weights for its assigned heads.
Output projection:        row-split, all-reduce after.
KV cache:                 sharded — each GPU only stores KV for its own heads.
```

**Embedding / LM head** (split across vocabulary):

```
Qwen2.5-72B vocab: 152,064 tokens
TP=16:
  GPU 0:   token IDs     0 –  9503  (152064 ÷ 16)
  GPU 1:   token IDs  9504 – 19007
  ...
  GPU 15:  token IDs 142560 – 152063

Logit computation: each GPU computes logits for its vocab slice,
then all-reduce max + softmax to pick the next token.
```

### Loading process step by step

```
t=0   vLLM starts on pod 0 (Ray head, TP ranks 0–7)
      vLLM starts on pod 1 (Ray worker, TP ranks 8–15)
      vLLM workers discover each other through the Ray GCS on pod 0.

t=1   Each node independently downloads the checkpoint from HuggingFace.
      The checkpoint arrives in CPU RAM (not GPU HBM yet).
      Both nodes fetch the full ~144 GB; download can be parallelized.

t=2   vLLM slices each tensor based on the worker's TP rank.
      Example for W_gate on a transformer layer:
        rank 0:  tensor[:, 0:1376]       loaded to GPU 0 HBM
        rank 1:  tensor[:, 1376:2752]    loaded to GPU 1 HBM
        ...
      Non-local columns are discarded — never move to GPU.

t=3   KV cache is allocated from remaining HBM on each GPU
      (≈90% utilization → ~63 GB KV cache per GPU after 9 GB weights).

t=4   Readiness probe passes.
```

The full checkpoint passes through CPU RAM on both nodes during loading, but GPU HBM only ever holds 1/16th of the model per GPU. The two nodes never send weights to each other — weights are sharded statically at load time and stay put for the lifetime of the server.

### What does cross the NVLink fabric

Only **activations** travel over NVLink during inference — never weights.

```
Per token, per transformer layer (80 layers in Qwen2.5-72B):

  1 all-reduce after attention output projection  (~8 KB at hidden=8192 FP16)
  1 all-reduce after MLP W_down                   (~8 KB)
  ──────────────────────────────────────────────────
  ~16 KB per layer × 80 layers = ~1.3 MB per token per forward pass

At NVLink Switch 1.8 TB/s:  1.3 MB ÷ 1.8 TB/s ≈ 0.7 µs total NVLink time
```

This is why high-bandwidth NVLink enables high tensor parallelism: the activation volume is tiny relative to the available bandwidth.

---

## Nebius resource model

Nebius exposes the NVLink fabric through two linked resources:

```
ComputeV1NvlInstanceGroup
  - parent_id:  Nebius project
  - type:       GB200         # NVLink fabric topology
  - size:       2             # max nodes in the fabric

Mk8sV1NodeGroup (gpu-nvlink)
  - template.resources.platform: gpu-h100-sxm
  - template.resources.preset:   8gpu-640gb
  - template.nvlink.nvl_instance_group_id: → NvlInstanceGroup.id
```

Nebius provisions the NVLink Switch fabric **before** nodes join the Kubernetes cluster. Nodes in the same `NvlInstanceGroup` are co-located in the same rack and wired to the same NVLink Switch hardware. This is invisible to Kubernetes — from the cluster's perspective they are regular nodes; from NCCL's perspective all peer-to-peer transfers use NVLink.

The Pulumi module creates `ComputeV1NvlInstanceGroup` resources first, then passes their IDs to the node group creation call. The `nvlink_groups` section in `config.yaml` drives both:

```yaml
nvlink_groups:
  nvlink-h100:
    type: GB200
    size: 2

node_groups:
  gpu-nvlink:
    platform: gpu-h100-sxm
    preset: 8gpu-640gb
    desired_size: 2
    min_size: 2
    max_size: 2
    nvlink_group: nvlink-h100   # ← links to the NvlInstanceGroup above
```

---

## Kubernetes deployment architecture

```
┌──────────────────────────────────────────────────────────────┐
│  StatefulSet: vllm-nvlink  (replicas=2, serviceName=headless)│
│                                                              │
│  Pod 0  vllm-nvlink-0              Pod 1  vllm-nvlink-1      │
│  ┌──────────────────────┐          ┌────────────────────┐    │
│  │ Ray head             │          │ Ray worker         │    │
│  │ vLLM serve TP=16     │          │ (blocks on join)   │    │
│  │ port 8000 (OpenAI)   │◄─NVLink─►│                    │    │
│  │ port 6379 (Ray GCS)  │          │ port 6379 (Ray)    │    │
│  │ 8 × H100 SXM (DRA)  │          │ 8 × H100 SXM (DRA) │    │
│  └──────────────────────┘          └────────────────────┘    │
└──────────────────────────────────────────────────────────────┘

Services
  vllm-nvlink-headless (clusterIP: None)
    → both pods, ports 6379 + 8265 (Ray peer discovery)

  vllm-nvlink (ClusterIP)
    → pod 0 only (statefulset.kubernetes.io/pod-name: vllm-nvlink-0)
    → port 8000 (OpenAI-compatible inference endpoint)

HTTPRoute: inference-nvlink.nebius.internal → vllm-nvlink:8000
```

### GPU allocation via DRA

Each pod requests 8 GPUs through a `ResourceClaimTemplate`:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: vllm-nvlink-gpus
spec:
  spec:
    devices:
      requests:
      - name: gpus
        exactly:
          deviceClassName: gpu.nvidia.com
          count: 8
```

The NVIDIA DRA driver allocates 8 physical GPUs per pod, matching the 8 GPUs on each node. The `resources.claims` field in the container spec references this claim:

```yaml
resources:
  claims:
  - name: gpus
```

DRA is used instead of `nvidia.com/gpu` limits because it allows fine-grained allocation (exact count, specific GPU topology) without relying on the device plugin model.

---

## Pod startup sequence

```
t=0   Kubernetes schedules pod 0 → node 0, pod 1 → node 1
      (NVLink fabric already active — provisioned by Nebius before node join)

t=5s  Pod 0: ray start --head --port=6379 --num-gpus=8
      Pod 1: ray start --address=vllm-nvlink-0.vllm-nvlink-headless.<ns>.svc:6379

t=10s Ray cluster formed: 2 nodes, 16 GPUs total visible to the scheduler

t=15s Pod 0: ray status reports 2 nodes — launches:
      vllm serve Qwen/Qwen2.5-72B-Instruct --tensor-parallel-size=16

t=~15m vLLM downloads weights (first start) and shards them across 16 GPUs
       Each GPU holds 1/16 of every weight matrix (~9 GB per GPU for 72B FP16)

t=~16m Readiness probe passes → Service routes traffic to pod 0:8000
```

On subsequent restarts, weight download is skipped if the `HF_HOME` volume is backed by persistent storage.

---

## Request flow

```
Client (HTTP)
  → kgateway  (inference-nvlink.nebius.internal)
  → vllm-nvlink Service  (ClusterIP → pod 0 :8000)
  → vLLM API server  (pod 0)
       │
       │  prefill + decode: TP=16 all-reduce each layer
       │
       ├── GPU 0–7  (pod 0, node 0)  ──NVLink Switch──►
       └── GPU 8–15 (pod 1, node 1)
              ↕ NCCL all-reduce (NVLink, ~microseconds per layer)
              ↕ output logits gathered to GPU 0 (pod 0)
  ← streaming token response
```

NCCL uses the NVLink transport end-to-end. The peer-to-peer memory path between pods goes: `GPU → NVLink Switch → GPU` without involving the CPU or host network stack.

---

## Load balancing

There are three distinct layers of "load balancing" in this design, and they operate on completely different principles.

### Layer 1 — network routing (kgateway → pod 0)

This is trivial and deliberate: **all requests go to pod 0**. The ClusterIP service selects pod 0 exclusively via the `statefulset.kubernetes.io/pod-name: vllm-nvlink-0` label. Pod 1 never receives HTTP traffic.

This is not a limitation — it reflects the architecture. There is one vLLM process (on pod 0) and it already uses all 16 GPUs for every request. Routing some requests to pod 1 would have nowhere useful to go: pod 1 only runs a Ray worker and has no API server.

```
All clients → kgateway → vllm-nvlink Service → pod 0:8000 (only)
                                                     │
                                             vLLM API server
                                             (single entry point)
```

### Layer 2 — vLLM continuous batching scheduler (within pod 0)

The actual "load balancing" of concurrent requests happens inside the vLLM scheduler on pod 0. vLLM uses **continuous batching** (also called in-flight batching): rather than waiting for a request to fully complete before starting the next one, the scheduler merges multiple requests into each GPU forward pass.

```
Arrival stream:  req-A (1024 tokens)  req-B (512 tokens)  req-C (256 tokens)
                       ↓
                 vLLM scheduler (pod 0)
                       │
              Iteration 0 (prefill batch):
                req-A prefill:   1024 tokens batched into one forward pass
                req-B prefill:   512  tokens batched in same forward pass
                req-C prefill:   256  tokens batched in same forward pass
                All 16 GPUs run one batched matrix multiply across all prompts.
                       │
              Iteration 1 (decode):
                req-A decode:  token 1025  ─┐
                req-B decode:  token 513   ─┤  batched into one forward pass
                req-C decode:  token 257   ─┘
                One pass, all 16 GPUs, three sequences simultaneously.
                       │
              Iteration N:
                req-A completes → slot freed → req-D admitted from queue
```

Every forward pass — whether batched across 1 request or 50 — uses all 16 GPUs in lock-step via NVLink. The scheduler does not assign "some GPUs to req-A and other GPUs to req-B". The GPUs are not independent workers; they are all co-executing every batch together.

**KV cache is the true bottleneck.** Each running request holds KV cache proportional to its current sequence length. When the combined KV cache of all in-flight requests fills the available HBM (≈63 GB per GPU × 16 = ~1 TB total KV), the scheduler queues new requests rather than admitting them. This is the mechanism that bounds concurrency.

```
KV cache capacity (Qwen2.5-72B, TP=16, 90% utilization):

  Per GPU HBM:            80 GB
  Weight shard per GPU:    9 GB
  Available for KV:       ~63 GB  (80 × 0.90 − 9)
  Total KV pool:         ~1 TB   (63 × 16)

  KV size per token per layer:
    2 × (num_kv_heads / TP) × head_dim × sizeof(FP16)
    = 2 × (8/16) × 128 × 2 = 256 bytes
    × 80 layers = 20 KB per token

  Max concurrent context:  ~1 TB ÷ 20 KB = ~50 million tokens
  (e.g., 1000 concurrent requests each with 50K context)
```

In practice, throughput is GPU compute–bound (not KV-bound) for standard context lengths with this configuration.

### Layer 3 — horizontal scaling with multiple replicas

A single TP=16 group services all traffic through continuous batching. If the total request rate exceeds what one group can handle (typically when GPU utilization is sustained at 100% and queue depth grows), the solution is **replica scaling**: deploy a second independent TP=16 group and load-balance across both at the gateway layer.

```
                    kgateway
                   /         \
          replica-0            replica-1
     (StatefulSet A)        (StatefulSet B)
     pod-A0 + pod-A1        pod-B0 + pod-B1
     16 × H100 (NVLink)     16 × H100 (NVLink)
     full model copy         full model copy
         TP=16                   TP=16
```

Each replica is an independent TP group with its own full weight shard set. Replicas do not share KV cache or coordinate with each other — they are fully independent. kgateway routes requests across replicas using round-robin or least-connections.

This requires a second `NvlInstanceGroup` (another pair of nodes), a second StatefulSet, and a second set of `ResourceClaims`. Each replica holds its own copy of the weight shards (neither replica has the other's shards). Total GPU count doubles; total memory doubles; maximum concurrent context doubles.

```yaml
# config.yaml for two replicas
nvlink_groups:
  nvlink-h100-0:
    type: GB200
    size: 2
  nvlink-h100-1:
    type: GB200
    size: 2

node_groups:
  gpu-nvlink-0: { nvlink_group: nvlink-h100-0, ... }
  gpu-nvlink-1: { nvlink_group: nvlink-h100-1, ... }
```

### Summary

| Layer | Mechanism | Unit of distribution |
|---|---|---|
| kgateway → pod | Fixed: all traffic to pod 0 | Not distributed (by design) |
| pod 0 → GPUs | TP all-reduce, every request uses all 16 GPUs | Forward pass (batched) |
| Concurrent requests | Continuous batching in vLLM scheduler | Token (per iteration) |
| Scale-out | Multiple independent TP replicas behind kgateway | Request (round-robin) |

The key mental model: **TP is not load balancing — it is parallelism**. Every request recruits all 16 GPUs simultaneously. Load balancing in the traditional sense (distributing work across independent workers) only appears at the replica level, outside the TP group.

---

## NCCL transport selection

NCCL probes the system topology at startup and builds a transport plan:

| Path | NCCL transport | Trigger |
|---|---|---|
| GPU ↔ GPU, same node, NVLink | `NVLS` (NVLink SHARP) | cuda topo file shows NVLink |
| GPU ↔ GPU, cross-node, NVLink Switch | `NVLS` | NVLink Switch in topology |
| GPU ↔ GPU, cross-node, InfiniBand | `NET/IB` | `NCCL_IB_DISABLE=0` |
| GPU ↔ GPU, cross-node, TCP | `NET/Socket` | fallback |

On Nebius NVLink nodes, CUDA exposes the Switch fabric as NVLink peers. NCCL selects `NVLS` automatically — no `NCCL_P2P_NET_CHUNKSIZE`, `FI_EFA_*`, or IB plugin configuration is required. The `NCCL_DEBUG=WARN` env var in the StatefulSet lets you inspect the selected transport in pod logs at startup.

---

## Model capacity

With 16 × H100 SXM 80 GB (1,280 GB total HBM):

| Model | Size | Format | GPU memory | Fits? |
|---|---|---|---|---|
| Qwen2.5-72B-Instruct | 72B | FP16 | ~144 GB | Yes (~11% each GPU) |
| Llama-3.1-405B | 405B | FP8 | ~405 GB | Yes (~32% each GPU) |
| Llama-3.1-405B | 405B | FP16 | ~810 GB | Yes (~63% each GPU) |
| DeepSeek-R1 | 671B | FP8 | ~671 GB | Yes (~52% each GPU) |

The remaining HBM is used for KV cache. At `--gpu-memory-utilization=0.90`, roughly 450 GB is available for KV cache on the 72B FP16 configuration — enough for very long context or high concurrency.

---

## Comparison with pipeline-parallel over EFA (AWS 32B design)

| | NVLink TP=16 (this design) | EFA PP=2 TP=4 (AWS 32B) |
|---|---|---|
| Model size | 70B–405B | ~32B |
| All-reduce bandwidth | 1.8 TB/s (NVLink Switch) | ~50 GB/s (EFA) |
| PP pipeline bubble | None | ~25–30% idle time |
| TTFT (time to first token) | Low — no PP stage delay | Higher — PP depth adds latency |
| Throughput (tokens/s) | High — NVLink keeps compute saturated | Lower — EFA bandwidth limits large TP |
| Config complexity | Low (TP only) | Higher (PP+TP, Ray, EFA plugins) |
| Preemptible nodes | No (NVLink fabric is fixed-size) | Yes |
| Cost model | Reserved, high GPU density | Spot-eligible |

---

## Files

```
iac-modules/cluster-infra/nebius-mk8s-v1.34-v1/
  main.py                          # creates ComputeV1NvlInstanceGroup, passes IDs to node groups
  node_groups/node_groups.py       # wires Mk8sV1NodeGroupTemplateNvlinkArgs per node group

iac-modules/extensions/vllm-nvlink/v0.9-v1/
  statefulset.yaml                 # 2-pod StatefulSet, Ray head/worker entrypoint
  resourceclaimtemplate.yaml       # DRA: 8 GPUs per pod
  service.yaml                     # headless (Ray) + ClusterIP (inference)
  httproute.yaml                   # inference-nvlink.nebius.internal
  kustomization.yaml

clusters/nebius-alpha/
  config.yaml                      # nvlink_groups + gpu-nvlink node group
  extensions/gpu/vllm-nvlink/      # Flux overlay pointing at the extension above
  extensions/gpu.yaml              # Flux Kustomization for the gpu layer
```
