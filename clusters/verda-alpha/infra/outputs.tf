output "control_plane" {
  value = module.cluster.control_plane
}

output "workers" {
  value = module.cluster.workers
}

output "cluster_endpoint" {
  value = module.cluster.cluster_endpoint
}

output "api_server_public_url" {
  value = module.cluster.api_server_public_url
}

output "kubeconfig_path" {
  value = module.cluster.kubeconfig_path
}

output "gateway_hostname" {
  value = module.cluster.gateway_hostname
}
