# Verda kubeadm cluster — 1 control plane + N worker nodes.
#
# Verda has no managed Kubernetes and no custom-image import, so this module
# does what image-builder would have baked into an image, in place: every
# instance boots a stock Ubuntu image with a startup script that runs
# kubernetes-sigs/image-builder's own Ansible roles (setup/node/containerd/
# kubernetes) against localhost. Terraform then drives kubeadm init/join over
# SSH and bootstraps Cilium + Flux from the control plane, after which the
# cluster's extensions tree (Flux) owns the add-ons.

locals {
  kubernetes_series = "v${join(".", slice(split(".", var.kubernetes_version), 0, 2))}"

  # Flatten node_groups into one instance per (group, index): "core-1", "core-2", ...
  workers = merge([
    for group, cfg in var.node_groups : {
      for i in range(cfg.size) : "${group}-${i + 1}" => merge(cfg, { group = group })
    }
  ]...)

  ssh_private_key_path = pathexpand(var.ssh_private_key_path)
  ssh_private_key      = file(local.ssh_private_key_path)

  # Variables image-builder's packer templates normally pass with
  # --extra-vars (packer/config/*.json + ansible-args.json), pinned to 1.35.
  image_builder_vars = {
    build_target        = "raw"
    packer_builder_type = ""
    packer_build_name   = ""
    python_path         = ""
    custom_role_names   = ""
    node_ansible_tmpdir = ""

    kubernetes_source_type                      = "pkg"
    kubernetes_cni_source_type                  = "pkg"
    kubernetes_series                           = local.kubernetes_series
    kubernetes_semver                           = "v${var.kubernetes_version}"
    kubernetes_deb_version                      = "${var.kubernetes_version}-1.1"
    kubernetes_deb_repo                         = "https://pkgs.k8s.io/core:/stable:/${local.kubernetes_series}/deb/"
    kubernetes_deb_gpg_key                      = "https://pkgs.k8s.io/core:/stable:/${local.kubernetes_series}/deb/Release.key"
    kubernetes_cni_deb_version                  = var.kubernetes_cni_deb_version
    kubernetes_cni_semver                       = "v${split("-", var.kubernetes_cni_deb_version)[0]}"
    kubernetes_cni_http_source                  = "https://github.com/containernetworking/plugins/releases/download"
    kubernetes_http_source                      = "https://dl.k8s.io/release"
    kubernetes_container_registry               = "registry.k8s.io"
    kubernetes_apiserver_port                   = "6443"
    kubeadm_template                            = "etc/kubeadm.yml"
    kubernetes_load_additional_imgs             = false
    kubernetes_enable_automatic_resource_sizing = false
    crictl_version                              = var.crictl_version

    containerd_version                     = var.containerd_version
    runc_version                           = var.runc_version
    containerd_service_url                 = "https://raw.githubusercontent.com/containerd/containerd/refs/tags/v${var.containerd_version}/containerd.service"
    containerd_cri_socket                  = "/var/run/containerd/containerd.sock"
    containerd_additional_settings         = "" # packer renders the JSON null as "", and the template b64decodes it
    containerd_enable_limit_no_file        = false
    containerd_gvisor_runtime              = false
    containerd_gvisor_version              = "latest"
    containerd_image_pull_progress_timeout = ""
    containerd_wasm_shims_runtimes         = ""
    containerd_wasm_shims_runtime_versions = "{}"
    containerd_wasm_shims_sha256           = "{}"
    containerd_wasm_shims_url              = ""
    containerd_wasm_shims_version          = ""
    enable_containerd_audit                = false
    pause_image                            = "registry.k8s.io/pause:3.10.2"

    systemd_prefix     = "/usr/lib/systemd"
    sysusr_prefix      = "/usr"
    sysusrlocal_prefix = "/usr/local"

    ubuntu_repo                = "http://archive.ubuntu.com/ubuntu/"
    ubuntu_security_repo       = "http://security.ubuntu.com/ubuntu/"
    disable_public_repos       = false
    reenable_public_repos      = true
    remove_extra_repos         = false
    extra_repos                = ""
    extra_debs                 = ""
    extra_kernel_boot_params   = ""
    netplan_removal_excludes   = ""
    pip_conf_file              = ""
    http_proxy                 = ""
    https_proxy                = ""
    no_proxy                   = ""
    debug_tools                = false
    gpu_block_nouveau_loading  = false
    load_additional_components = false
    ecr_credential_provider    = false
    image_builder_version      = var.image_builder_ref
  }

  node_startup_script = templatefile("${path.module}/scripts/node-startup.sh.tftpl", {
    image_builder_ref    = var.image_builder_ref
    ansible_core_version = var.ansible_core_version
    helm_version         = var.helm_version
    vars_json            = jsonencode(local.image_builder_vars)
    playbook             = file("${path.module}/ansible/verda-node.yml")
  })
}

# ── Startup script (shared by every node) ─────────────────────────────────────
# Verda runs it once, as root, on first boot. The name carries a content hash
# so a script change yields a new script (and therefore new instances) rather
# than a silent in-place edit that existing nodes would never pick up.

# The provider refuses in-place updates ("Startup scripts cannot be updated"),
# so a content change has to replace the resource; tie that to a hash.
resource "terraform_data" "node_startup_script_hash" {
  input = sha256(local.node_startup_script)
}

resource "verda_startup_script" "node" {
  name   = "${var.cluster_name}-node-${substr(sha256(local.node_startup_script), 0, 8)}"
  script = local.node_startup_script

  lifecycle {
    create_before_destroy = true
    replace_triggered_by  = [terraform_data.node_startup_script_hash]
  }
}

# ── Control plane ─────────────────────────────────────────────────────────────

resource "verda_instance" "control_plane" {
  hostname      = "${var.cluster_name}-cp-1"
  description   = "${var.cluster_name} Kubernetes control plane (kubeadm ${var.kubernetes_version})"
  instance_type = var.control_plane.instance_type
  image         = var.control_plane.image
  location      = var.location
  is_spot       = var.control_plane.is_spot

  ssh_key_ids       = var.ssh_key_ids
  startup_script_id = verda_startup_script.node.id

  os_volume = {
    name = "${var.cluster_name}-cp-1-os"
    size = var.control_plane.os_volume_size
    type = "NVMe"
  }

  lifecycle {
    # The startup script only runs on first boot, so a script change is a
    # node-image change: it applies to new/replaced nodes, never rebuilds the
    # running ones (same semantics as a launch-template bump without instance
    # refresh). Rebuild deliberately with `terraform apply -replace=...`.
    ignore_changes = [startup_script_id]
  }
}

# The provider returns from Create as soon as the order is accepted, before the
# instance is `running` or has an IP, so poll the API until it is. Also the only
# way to get private_ip, which the provider schema doesn't expose.
data "external" "control_plane" {
  program = ["bash", "${path.module}/scripts/wait-for-instance.sh"]
  query = {
    instance_id = verda_instance.control_plane.id
    timeout     = "900"
  }
}

locals {
  cp_public_ip  = data.external.control_plane.result.ip
  cp_private_ip = data.external.control_plane.result.private_ip
  # Nodes talk to the API server over the private network when Verda gives us
  # one; otherwise fall back to the public address.
  cp_endpoint_ip = local.cp_private_ip != "" ? local.cp_private_ip : local.cp_public_ip
}

resource "terraform_data" "kubeadm_init" {
  triggers_replace = [verda_instance.control_plane.id]

  connection {
    type        = "ssh"
    host        = local.cp_public_ip
    user        = var.ssh_user
    private_key = local.ssh_private_key
    timeout     = "15m"
  }

  provisioner "file" {
    source      = "${path.module}/scripts/wait-for-node-build.sh"
    destination = "/root/wait-for-node-build.sh"
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/kubeadm-init.yaml.tftpl", {
      cluster_name       = var.cluster_name
      kubernetes_version = var.kubernetes_version
      advertise_ip       = local.cp_endpoint_ip
      public_ip          = local.cp_public_ip
      hostname           = verda_instance.control_plane.hostname
      pod_cidr           = var.pod_cidr
      service_cidr       = var.service_cidr
    })
    destination = "/root/kubeadm-init.yaml"
  }

  provisioner "remote-exec" {
    inline = [
      "bash /root/wait-for-node-build.sh",
      # Idempotent: a re-run against an already-initialised node is a no-op.
      "test -f /etc/kubernetes/admin.conf || kubeadm init --config /root/kubeadm-init.yaml",
    ]
  }
}

# admin.conf + CA public-key hash, fetched over SSH once init has run.
data "external" "cluster_access" {
  program = ["bash", "${path.module}/scripts/fetch-cluster-access.sh"]
  query = {
    host             = local.cp_public_ip
    user             = var.ssh_user
    private_key_path = local.ssh_private_key_path
    # Not used by the script; ties the read to the init run so it's re-read
    # after a control-plane rebuild.
    init_id = terraform_data.kubeadm_init.id
  }
  depends_on = [terraform_data.kubeadm_init]
}

locals {
  admin_kubeconfig = base64decode(data.external.cluster_access.result.kubeconfig_b64)
  ca_cert_hash     = data.external.cluster_access.result.ca_cert_hash
}

resource "local_sensitive_file" "kubeconfig" {
  filename        = var.kubeconfig_output_path
  file_permission = "0600"
  # admin.conf points at the advertise (private) address; the public IP is in
  # the API server's certSANs so the same credentials work from outside.
  content = replace(
    local.admin_kubeconfig,
    "https://${local.cp_endpoint_ip}:6443",
    "https://${local.cp_public_ip}:6443",
  )
}

# ── Workers ───────────────────────────────────────────────────────────────────

resource "verda_instance" "worker" {
  for_each = local.workers

  hostname      = "${var.cluster_name}-${each.key}"
  description   = "${var.cluster_name} worker (${each.value.group})"
  instance_type = each.value.instance_type
  image         = each.value.image
  location      = var.location
  is_spot       = each.value.is_spot

  ssh_key_ids       = var.ssh_key_ids
  startup_script_id = verda_startup_script.node.id

  os_volume = {
    name = "${var.cluster_name}-${each.key}-os"
    size = each.value.os_volume_size
    type = "NVMe"
  }

  lifecycle {
    # See control_plane: script changes roll out on replacement only.
    ignore_changes = [startup_script_id]
  }
}

data "external" "worker" {
  for_each = local.workers

  program = ["bash", "${path.module}/scripts/wait-for-instance.sh"]
  query = {
    instance_id = verda_instance.worker[each.key].id
    timeout     = "900"
  }
}

# One bootstrap token per worker: "<6 chars>.<16 chars>", created on the control
# plane right before the worker joins and expiring on its own afterwards.
resource "random_string" "join_token_id" {
  for_each = local.workers
  length   = 6
  lower    = true
  upper    = false
  numeric  = true
  special  = false
}

resource "random_password" "join_token_secret" {
  for_each = local.workers
  length   = 16
  lower    = true
  upper    = false
  numeric  = true
  special  = false
}

resource "terraform_data" "kubeadm_join" {
  for_each = local.workers

  triggers_replace = [
    verda_instance.worker[each.key].id,
    terraform_data.kubeadm_init.id,
  ]

  # Only `self` may be referenced from a destroy-time provisioner, so stash
  # what the node-removal step needs.
  input = {
    node_name            = verda_instance.worker[each.key].hostname
    cp_host              = local.cp_public_ip
    ssh_user             = var.ssh_user
    ssh_private_key_path = local.ssh_private_key_path
  }

  # 1) Mint this worker's bootstrap token on the control plane.
  provisioner "remote-exec" {
    connection {
      type        = "ssh"
      host        = local.cp_public_ip
      user        = var.ssh_user
      private_key = local.ssh_private_key
      timeout     = "5m"
    }
    inline = [
      "kubeadm token delete ${random_string.join_token_id[each.key].result} >/dev/null 2>&1 || true",
      "kubeadm token create ${random_string.join_token_id[each.key].result}.${random_password.join_token_secret[each.key].result} --ttl 2h --description 'terraform join: ${each.key}' >/dev/null",
    ]
  }

  # 2) Join the worker. (Per-provisioner connections rather than a resource-level
  #    one: the destroy-time provisioner below would inherit a resource-level
  #    block, and those may only reference `self`.)
  provisioner "file" {
    connection {
      type        = "ssh"
      host        = data.external.worker[each.key].result.ip
      user        = var.ssh_user
      private_key = local.ssh_private_key
      timeout     = "15m"
    }
    source      = "${path.module}/scripts/wait-for-node-build.sh"
    destination = "/root/wait-for-node-build.sh"
  }

  provisioner "file" {
    connection {
      type        = "ssh"
      host        = data.external.worker[each.key].result.ip
      user        = var.ssh_user
      private_key = local.ssh_private_key
      timeout     = "5m"
    }
    content = templatefile("${path.module}/templates/kubeadm-join.yaml.tftpl", {
      api_server_endpoint = "${local.cp_endpoint_ip}:6443"
      token               = "${random_string.join_token_id[each.key].result}.${random_password.join_token_secret[each.key].result}"
      ca_cert_hash        = local.ca_cert_hash
      hostname            = verda_instance.worker[each.key].hostname
      node_ip = (
        data.external.worker[each.key].result.private_ip != ""
        ? data.external.worker[each.key].result.private_ip
        : data.external.worker[each.key].result.ip
      )
      labels = merge(each.value.labels, { "node-group" = each.value.group })
      taints = each.value.taints
    })
    destination = "/root/kubeadm-join.yaml"
  }

  provisioner "remote-exec" {
    connection {
      type        = "ssh"
      host        = data.external.worker[each.key].result.ip
      user        = var.ssh_user
      private_key = local.ssh_private_key
      timeout     = "5m"
    }
    inline = [
      "bash /root/wait-for-node-build.sh",
      "test -f /etc/kubernetes/kubelet.conf || kubeadm join --config /root/kubeadm-join.yaml",
    ]
  }

  # 3) On removal, drop the Node object so it doesn't linger as NotReady.
  provisioner "remote-exec" {
    when       = destroy
    on_failure = continue
    connection {
      type        = "ssh"
      host        = self.input.cp_host
      user        = self.input.ssh_user
      private_key = file(self.input.ssh_private_key_path)
      timeout     = "2m"
    }
    inline = [
      "kubectl --kubeconfig /etc/kubernetes/admin.conf delete node ${self.input.node_name} --ignore-not-found",
    ]
  }
}

# ── Add-ons: Cilium + Flux ────────────────────────────────────────────────────
# Installed from the control plane with the same release names the cluster's
# Flux extensions use (cilium/kube-system, fluxcd/flux-system), so Flux adopts
# and reconciles them from git afterwards.

data "sops_file" "flux_git_secret" {
  source_file = var.flux_git_secret_values_path
}

# Reads $SOPS_AGE_KEY_FILE (the convention every other cluster here uses)
# without needing it as a Terraform variable.
data "external" "sops_age_key" {
  program = ["bash", "${path.module}/scripts/read-env-file.sh"]
  query   = { env_var = "SOPS_AGE_KEY_FILE" }
}

locals {
  flux_values = yamlencode(yamldecode(file(var.flux_values_path)).spec.values)

  cilium_values = templatefile("${path.module}/templates/cilium-values.yaml.tftpl", {
    cluster_name = var.cluster_name
    endpoint_ip  = local.cp_endpoint_ip
    pod_cidr     = var.pod_cidr
  })

  flux_bootstrap_manifests = templatefile("${path.module}/templates/flux-bootstrap.yaml.tftpl", {
    cluster_name     = var.cluster_name
    cluster_endpoint = local.cp_endpoint_ip
    git_secret_name  = var.flux_git_secret_name
    github_username  = trimspace(data.sops_file.flux_git_secret.data["github.username"])
    github_token     = trimspace(data.sops_file.flux_git_secret.data["github.token"])
    sops_secret_name = var.flux_sops_secret_name
    sops_age_key     = data.external.sops_age_key.result.content
  })

  flux_sync_manifests = templatefile("${path.module}/templates/flux-sync.yaml.tftpl", {
    git_url                = var.flux_git_url
    git_branch             = var.flux_git_branch
    git_path               = var.flux_git_path
    git_secret_name        = var.flux_git_secret_name
    git_interval           = var.flux_git_interval
    kustomization_interval = var.flux_kustomization_interval
    sops_secret_name       = data.external.sops_age_key.result.content != "" ? var.flux_sops_secret_name : ""
  })
}

resource "terraform_data" "addons" {
  triggers_replace = [
    terraform_data.kubeadm_init.id,
    var.cilium_version,
    var.flux_chart_version,
    sha256(local.cilium_values),
    sha256(local.flux_values),
    sha256(local.flux_bootstrap_manifests),
    sha256(local.flux_sync_manifests),
    filesha256("${path.module}/scripts/bootstrap-addons.sh"),
  ]

  # Flux's controllers pin to node-type=core workers, so wait for the joins.
  depends_on = [terraform_data.kubeadm_join]

  connection {
    type        = "ssh"
    host        = local.cp_public_ip
    user        = var.ssh_user
    private_key = local.ssh_private_key
    timeout     = "5m"
  }

  provisioner "remote-exec" {
    inline = ["mkdir -p /root/bootstrap && chmod 700 /root/bootstrap"]
  }

  provisioner "file" {
    source      = "${path.module}/scripts/bootstrap-addons.sh"
    destination = "/root/bootstrap/bootstrap-addons.sh"
  }

  provisioner "file" {
    content     = local.cilium_values
    destination = "/root/bootstrap/cilium-values.yaml"
  }

  provisioner "file" {
    content     = local.flux_values
    destination = "/root/bootstrap/flux-values.yaml"
  }

  provisioner "file" {
    content     = local.flux_bootstrap_manifests
    destination = "/root/bootstrap/flux-bootstrap.yaml"
  }

  provisioner "file" {
    content     = local.flux_sync_manifests
    destination = "/root/bootstrap/flux-sync.yaml"
  }

  provisioner "remote-exec" {
    inline = [
      "CILIUM_VERSION='${var.cilium_version}' FLUX_CHART_VERSION='${var.flux_chart_version}' bash /root/bootstrap/bootstrap-addons.sh",
    ]
  }
}
