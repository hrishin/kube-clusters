# ── Cluster ──────────────────────────────────────────────────────────────────

variable "cluster_name" {
  description = "Cluster name; also the prefix for every Verda resource and the kubeadm clusterName."
  type        = string
}

variable "kubernetes_version" {
  description = "Full Kubernetes patch version to install (e.g. \"1.35.8\"). Must exist in pkgs.k8s.io for its minor series."
  type        = string
  validation {
    condition     = can(regex("^1\\.35\\.[0-9]+$", var.kubernetes_version))
    error_message = "This module is pinned to the 1.35 series (verda-kubeadm-v1.35-v1); use a 1.35.x version."
  }
}

variable "location" {
  description = "Verda datacenter location code (Verda's equivalent of a region). `verda locations` lists them; `verda availability` shows which instance types are in stock per location."
  type        = string
  default     = "FIN-03"
}

variable "pod_cidr" {
  description = "Pod network CIDR handed to kubeadm (networking.podSubnet) and to Cilium's cluster-pool IPAM."
  type        = string
  default     = "192.168.0.0/16"
}

variable "service_cidr" {
  description = "Service network CIDR handed to kubeadm (networking.serviceSubnet)."
  type        = string
  default     = "10.96.0.0/12"
}

# ── SSH ──────────────────────────────────────────────────────────────────────

variable "ssh_key_ids" {
  description = "Verda SSH key IDs to inject into every node (`verda ssh-key list`). The verda provider has no data source to look keys up by name, so IDs are passed explicitly."
  type        = list(string)
}

variable "ssh_private_key_path" {
  description = "Local private key matching one of ssh_key_ids; used by the kubeadm init/join provisioners and by the kubeconfig fetch."
  type        = string
  default     = "~/.ssh/id_rsa"
}

variable "ssh_user" {
  description = "SSH login user on Verda images."
  type        = string
  default     = "root"
}

# ── Nodes ────────────────────────────────────────────────────────────────────

variable "control_plane" {
  description = "Single control-plane node. instance_type must be in stock in `location` (`verda availability`)."
  type = object({
    instance_type  = string
    image          = optional(string, "ubuntu-24.04")
    os_volume_size = optional(number, 100)
    is_spot        = optional(bool, false)
  })
}

variable "node_groups" {
  description = <<-EOT
    Worker node groups, same schema shape as the other clusters in this repo.
    Each group expands to `size` instances named <cluster>-<group>-<n>.
    `instance_type` is the exact Verda type (e.g. CPU.16V.64G, 1RTXPRO6000.30V).
  EOT
  type = map(object({
    instance_type  = string
    image          = optional(string, "ubuntu-24.04")
    size           = optional(number, 1)
    os_volume_size = optional(number, 100)
    is_spot        = optional(bool, false)
    labels         = optional(map(string), {})
    taints = optional(list(object({
      key    = string
      value  = optional(string, "")
      effect = string
    })), [])
  }))
}

# ── Node build (kubernetes-sigs/image-builder) ───────────────────────────────

variable "image_builder_ref" {
  description = "kubernetes-sigs/image-builder git ref whose Ansible roles build the node in place."
  type        = string
  default     = "v0.1.55"
}

variable "ansible_core_version" {
  description = "ansible-core version installed on the node to run image-builder's roles (mirrors images/capi/hack/ensure-ansible.sh)."
  type        = string
  default     = "2.18.18"
}

variable "containerd_version" {
  type    = string
  default = "2.3.2"
}

variable "runc_version" {
  type    = string
  default = "1.4.3"
}

variable "crictl_version" {
  type    = string
  default = "1.35.0"
}

variable "kubernetes_cni_deb_version" {
  description = "kubernetes-cni deb version from the pkgs.k8s.io v1.35 repo."
  type        = string
  default     = "1.8.0-1.1"
}

variable "helm_version" {
  description = "Helm version installed on every node (used on the control plane to bootstrap Cilium + Flux)."
  type        = string
  default     = "3.19.0"
}

# ── Add-ons ──────────────────────────────────────────────────────────────────

variable "cilium_version" {
  type    = string
  default = "1.18.3"
}

variable "flux_chart_version" {
  type    = string
  default = "2.17.1"
}

variable "flux_values_path" {
  description = "Path to the fluxcd HelmRelease (iac-modules/extensions/fluxcd/<ver>/release.yaml); its spec.values are used for the bootstrap install so Flux can adopt the release later."
  type        = string
}

variable "flux_git_url" {
  type = string
}

variable "flux_git_branch" {
  type    = string
  default = "main"
}

variable "flux_git_path" {
  description = "Path inside the repo that the root Flux Kustomization reconciles (e.g. ./clusters/verda-alpha/extensions)."
  type        = string
}

variable "flux_git_secret_name" {
  type    = string
  default = "flux-system"
}

variable "flux_git_secret_values_path" {
  description = "SOPS-encrypted YAML holding github.username / github.token."
  type        = string
}

variable "flux_sops_secret_name" {
  description = "Name of the Secret holding the age identity for Flux decryption. The key content is read from $SOPS_AGE_KEY_FILE."
  type        = string
  default     = "sops-age"
}

variable "flux_git_interval" {
  type    = string
  default = "1m0s"
}

variable "flux_kustomization_interval" {
  type    = string
  default = "10m0s"
}

variable "kubeconfig_output_path" {
  description = "Where to write the admin kubeconfig (server rewritten to the control plane's public IP)."
  type        = string
}
