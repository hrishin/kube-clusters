output "cluster_name" {
  value = var.cluster_name
}

output "control_plane" {
  description = "Control-plane instance identity and addresses."
  value = {
    id         = verda_instance.control_plane.id
    hostname   = verda_instance.control_plane.hostname
    public_ip  = local.cp_public_ip
    private_ip = local.cp_private_ip
  }
}

output "workers" {
  description = "Worker instances keyed by <group>-<n>."
  value = {
    for k, w in verda_instance.worker : k => {
      id         = w.id
      hostname   = w.hostname
      group      = local.workers[k].group
      public_ip  = data.external.worker[k].result.ip
      private_ip = data.external.worker[k].result.private_ip
    }
  }
}

output "cluster_endpoint" {
  description = "API server address nodes use (private IP when available) — what Flux's infra-outputs CLUSTER_ENDPOINT is set to."
  value       = local.cp_endpoint_ip
}

output "api_server_public_url" {
  value = "https://${local.cp_public_ip}:6443"
}

output "kubeconfig_path" {
  value = local_sensitive_file.kubeconfig.filename
}

output "startup_script_id" {
  value = verda_startup_script.node.id
}
