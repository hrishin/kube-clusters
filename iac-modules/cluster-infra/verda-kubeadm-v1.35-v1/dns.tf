# Cloudflare A records for the gateway — the Terraform equivalent of the
# Pulumi modules' dns.py, except there is no LoadBalancer IP to wait for:
# the gateway is hostNetwork on the core nodes, so the nodes' public IPs are
# the endpoint and the records are known as soon as the instances are.

locals {
  dns_nodes = var.dns == null ? {} : {
    for k, w in local.workers : k => data.external.worker[k].result.ip
    if w.group == var.dns.node_group
  }
}

data "cloudflare_zones" "gateway" {
  count = var.dns == null ? 0 : 1
  name  = var.dns.zone
}

resource "cloudflare_dns_record" "gateway" {
  for_each = local.dns_nodes

  zone_id = data.cloudflare_zones.gateway[0].result[0].id
  name    = "${var.dns.name}.${var.dns.zone}"
  type    = "A"
  content = each.value
  ttl     = var.dns.ttl
  proxied = var.dns.proxied
  comment = "${var.cluster_name} gateway (${each.key})"
}
