# verda-alpha — entry point. Reads ../config.yaml and delegates to the
# verda-kubeadm module, mirroring how the Pulumi clusters wire config.yaml
# into their cluster-infra module.

locals {
  repo_root = "${path.module}/../../.."
  cfg       = yamldecode(file("${path.module}/../config.yaml"))
}

module "cluster" {
  source = "../../../iac-modules/cluster-infra/verda-kubeadm-v1.35-v1"

  cluster_name       = local.cfg.cluster_name
  kubernetes_version = local.cfg.kubernetes_version
  location           = local.cfg.location
  pod_cidr           = local.cfg.pod_cidr
  service_cidr       = local.cfg.service_cidr

  ssh_key_ids          = local.cfg.ssh_key_ids
  ssh_private_key_path = local.cfg.ssh_private_key_path

  control_plane = local.cfg.control_plane
  node_groups   = local.cfg.node_groups
  dns           = try(local.cfg.dns, null)

  flux_values_path            = "${local.repo_root}/iac-modules/extensions/fluxcd/v2.17.1-v1/release.yaml"
  flux_git_url                = local.cfg.flux_git_url
  flux_git_branch             = local.cfg.flux_git_branch
  flux_git_path               = local.cfg.flux_git_path
  flux_git_secret_name        = local.cfg.flux_git_secret_name
  flux_git_secret_values_path = "${local.repo_root}/config/config.enc.yaml"
  flux_sops_secret_name       = local.cfg.flux_sops_secret_name
  flux_git_interval           = local.cfg.flux_git_interval
  flux_kustomization_interval = local.cfg.flux_kustomization_interval

  # gitignored (kubeconfig.yaml)
  kubeconfig_output_path = "${path.module}/kubeconfig.yaml"
}
