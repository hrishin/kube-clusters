# verda-kubeadm-v1.35-v1

Terraform module: a kubeadm Kubernetes 1.35 cluster on Verda compute instances —
one control-plane node plus worker node groups — with Cilium and Flux bootstrapped.

Consumed by `clusters/verda-alpha/infra` (which feeds it `clusters/verda-alpha/config.yaml`).
Full operator guide: `docs/verda.md`.

## How it works

| Step | Mechanism |
|---|---|
| Instances | `verda_instance` (provider `verda-cloud/verda`), stock Ubuntu image, `verda_startup_script` shared by all nodes |
| Node build | Startup script installs pinned `ansible-core`, clones kubernetes-sigs/image-builder at `image_builder_ref`, runs `ansible/verda-node.yml` (image-builder's `setup/node/providers/containerd/kubernetes` roles) against localhost |
| Readiness | `scripts/wait-for-instance.sh` (`external` data source) polls the Verda API until `running`; `scripts/wait-for-node-build.sh` waits for the build marker on the node |
| Control plane | `terraform_data.kubeadm_init` → `kubeadm init --config` (`templates/kubeadm-init.yaml.tftpl`), kube-proxy addon skipped |
| Kubeconfig | `scripts/fetch-cluster-access.sh` pulls `admin.conf` + CA hash; written to `kubeconfig_output_path` with the public IP |
| Workers | `terraform_data.kubeadm_join` per node: token minted on the control plane, `kubeadm join --config` with labels/taints from config, node deleted on destroy |
| Add-ons | `terraform_data.addons` runs `scripts/bootstrap-addons.sh` on the control plane: Helm `cilium` (kube-system) + `fluxcd` (flux-system), `infra-outputs` ConfigMap, git/sops Secrets, GitRepository + root Kustomization |

Verda facts the design works around:

- No custom-image import → image-builder roles run in place instead of a prebuilt image.
- The provider's `Create` returns before the instance is running and never exposes `private_ip`.
- `private_ip` is nullable; when absent nodes use the public IP (`cluster_endpoint` output).
- Startup scripts run once, as root, **from cloud-init's runcmd** — never `cloud-init status --wait` inside one.
- Startup script changes are ignored on existing instances (`ignore_changes`); they apply to new/replaced nodes.

## Inputs of note

- `location` — Verda datacenter (its region); `verda availability` for stock.
- `control_plane.instance_type`, `node_groups.<g>.instance_type` — exact Verda types.
- `ssh_key_ids` — from `verda ssh-key list` (no data source exists to look them up).
- Env at apply time: `VERDA_CLIENT_ID`/`VERDA_CLIENT_SECRET` (`scripts/verda-env.sh`), `SOPS_AGE_KEY_FILE`.

## Versions pinned here

Kubernetes 1.35.x (deb `-1.1`), containerd 2.3.2, runc 1.4.3, crictl 1.35.0,
kubernetes-cni 1.8.0, image-builder v0.1.55, ansible-core 2.18.18, Helm 3.19.0,
Cilium 1.18.3, flux2 chart 2.17.1 — see `variables.tf`.
