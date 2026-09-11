# Verda kubeadm Cluster Setup

This document covers provisioning the `verda-alpha` cluster on [Verda](https://verda.com)
(formerly DataCrunch) with **Terraform** and **kubeadm**: one control-plane node plus
worker node groups, built from stock Ubuntu with kubernetes-sigs/image-builder's roles.

Unlike the other clusters in this repo it is not a Pulumi program — Verda has no
Pulumi provider, and the Terraform provider is what Verda documents
(<https://docs.verda.com/infrastructure-as-code/terraform/>).

## Why kubeadm (not Rancher)

- Verda has no managed Kubernetes for arbitrary instance shapes (Instant Clusters are
  fixed 8-GPU SKUs), so the cluster is self-managed either way.
- Every other cluster here is kubeadm-shaped (EKS' self-managed nodes, CAPS via CAPI's
  kubeadm bootstrap). The extension layer — Cilium with kube-proxy replacement, Flux,
  the base/overlay Kustomize pattern — carries over unchanged. Rancher/RKE2 would add a
  management plane and its own embedded CNI/ingress defaults to fight with.
- Three nodes don't justify a Rancher server; `kubeadm join` is the whole node lifecycle.

## Why image-builder roles instead of an image-builder image

Verda's API only *lists* images (`GET /v1/images`); there is no custom-image import, so
an image-builder artifact can't be uploaded. The module therefore runs the same Ansible
roles image-builder uses to produce its CAPI Ubuntu images (`setup`, `node`, `providers`,
`containerd`, `kubernetes`) against localhost from the instance's startup script — see
`iac-modules/cluster-infra/verda-kubeadm-v1.35-v1/ansible/verda-node.yml`. `sysprep` is
deliberately skipped (it wipes machine-id/SSH host keys/cloud-init state, which only
makes sense when producing a reusable image).

Verda's own `*.kubernetes-1.3x` images exist too but bundle an opaque, unversioned
node setup; image-builder pins containerd/runc/crictl/kubelet explicitly.

## Prerequisites

| Tool | Purpose |
|---|---|
| `terraform` >= 1.6 | Provisioning |
| `verda` CLI | Credentials, SSH key / instance-type / availability lookups |
| `sops` + `age` | Decrypts `config/config.enc.yaml` (GitHub token for Flux) |
| `jq`, `ssh`, `curl` | Used by the module's `external` data-source scripts |
| `kubectl` | Post-deploy verification |

```bash
for cmd in terraform verda sops age jq ssh kubectl; do
  command -v $cmd >/dev/null && echo "$cmd ✓" || echo "$cmd MISSING"
done
```

## 1. Credentials

Verda API keys are scoped to a **console project**; the key you export must come from
the project the cluster should live in (`verda_project` in `config.yaml`, currently
`k8s-gpu-cluster`). Save it as a CLI profile once:

```bash
verda auth login --profile k8s-gpu-cluster --client-id <id> --client-secret <secret>
# or just `verda auth login` if the default profile is that project already
```

The Terraform provider reads `VERDA_CLIENT_ID` / `VERDA_CLIENT_SECRET`; export them
from the CLI's credentials file rather than copying them anywhere:

```bash
eval "$(scripts/verda-env.sh)"                  # [default] profile
eval "$(scripts/verda-env.sh k8s-gpu-cluster)"  # named profile
```

Flux needs the SOPS age identity and the GitHub token, same as every other cluster:

```bash
export SOPS_AGE_KEY_FILE=~/.age/k8s-key.age     # decrypts config/config.enc.yaml
```

## 2. SSH key

Register your public key in Verda and put its ID in `config.yaml`:

```bash
verda ssh-key list          # → id of mac-os-public-key
```

`ssh_private_key_path` must be the matching private key; Terraform uses it for the
kubeadm provisioners (Verda images log in as `root`).

## 3. Configure

`clusters/verda-alpha/config.yaml` uses the same shape as the other clusters:

```yaml
location: FIN-03              # Verda's "region"; `verda locations`
control_plane:
  instance_type: CPU.8V.32G
node_groups:
  core:
    instance_type: CPU.8V.32G   # exact Verda type; `verda instance-types`
    size: 2
    labels: { node-type: core, ... }
    taints: [{ key: node-type, value: core, effect: NoSchedule }]
```

Check stock before choosing a type — availability varies per location:

```bash
verda availability
```

GPU workers: add a group with a GPU type (e.g. `1RTXPRO6000.30V`, `1H100.80S.30V`) and a
`*-cuda-*` image so the NVIDIA driver is preinstalled; run the `nvidia-gpu-operator`
extension with `driver.enabled=false`.

## 4. Deploy

```bash
cd clusters/verda-alpha/infra
eval "$(../../../scripts/verda-env.sh)"
terraform init
terraform apply
```

What happens, in order:

1. `verda_startup_script` + `verda_instance` × (1 + Σ size) are created.
2. Each node's startup script installs ansible-core, clones image-builder at the
   pinned ref and runs the node roles (≈10 min). Progress: `/var/log/verda-node-build.log`
   on the node; completion marker `/var/lib/image-builder/verda-node.done`.
3. `wait-for-instance.sh` polls the Verda API until each instance is `running` (the
   provider returns before that and never exposes `private_ip`).
4. `kubeadm init` on the control plane (advertises on the private IP when Verda
   assigns one, public IP in `certSANs`; `addon/kube-proxy` skipped for Cilium).
5. `admin.conf` is fetched to `clusters/verda-alpha/infra/kubeconfig.yaml` (gitignored),
   server rewritten to the public IP.
6. Each worker gets its own 2h bootstrap token minted on the control plane and runs
   `kubeadm join` with the CA hash, labels and taints from `config.yaml`.
7. From the control plane: `helm install cilium` and `helm install fluxcd` with the
   release names the Flux extensions expect, then the `infra-outputs` ConfigMap,
   `flux-system`/`sops-age` Secrets, GitRepository and root Kustomization.

```bash
export KUBECONFIG=$PWD/kubeconfig.yaml
kubectl get nodes -o wide
flux get kustomizations
```

## Observability

`extensions/system` mirrors `nebius-alpha/system-components`: kube-prometheus-stack
(Prometheus + operator, 2h local retention, remote_write to Mimir), Mimir (filesystem
backend, single replica), Grafana (Mimir + Tempo datasources, dashboard sidecar),
Tempo and the OTel collector DaemonSet. The one difference is placement — Nebius has
a dedicated `mimir` node group; here everything runs on the tainted `core` nodes.

- Grafana: <https://cluster1.fin-03.kube.verda.hrishi.dev/grafana>, user `admin`,
  password: `kubectl -n grafana get secret grafana -o jsonpath='{.data.admin-password}' | base64 -d`
- Mimir/Prometheus API via the gateway: `/mimir/prometheus`.

## Scaling / changing nodes

- `node_groups.<g>.size` up/down → `terraform apply`. Removed workers are
  `kubectl delete node`d by a destroy-time provisioner (best-effort).
- Changing the startup script (versions, playbook) replaces the `verda_startup_script`
  (Verda can't update them in place) but **does not rebuild running instances** — like a
  launch-template bump without instance refresh. New/replaced nodes pick it up; rebuild
  deliberately with `terraform apply -replace='module.cluster.verda_instance.worker["core-1"]'`.
- Verda CLI templates for ad-hoc nodes of the same shape live in
  `clusters/verda-alpha/templates/vm/` (`cp` them to `~/.verda/templates/vm/`).

## Teardown

```bash
terraform destroy
```

## Notes / known limits

- **Firewall**: Verda instances ship with no firewall; `6443` (API) and Cilium's ports
  are reachable on the public IP. Restrict with `ufw` or Verda's controls if that
  matters for your use.
- **LoadBalancer Services** stay `<pending>` — there is no Verda CCM. The cluster's
  kgateway (`extensions/infra/kgateway`) therefore runs its Envoy proxies `hostNetwork`
  on the core nodes (one per node, ports 80/443 on each node's public IP), and Terraform
  publishes those IPs as round-robin A records for `dns.name`.`dns.zone`
  (`cluster1.fin-03.kube.verda.hrishi.dev`). TLS comes from the `letsencrypt-cloudflare`
  ClusterIssuer; the Certificate's `dnsNames` must match `dns` in `config.yaml`.
  Scaling the `core` group adds/removes records on the next `terraform apply`.
- **Storage**: `local-path-provisioner` only (no CSI). Verda NVMe volumes can be
  attached with `verda_volume` if needed.
- `private_ip` is documented as nullable; when Verda doesn't put an instance on a
  private network the module falls back to the public IP for node-to-API traffic.
