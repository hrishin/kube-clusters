terraform {
  required_version = ">= 1.6"

  required_providers {
    verda = {
      source  = "verda-cloud/verda"
      version = "~> 1.1"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
    sops = {
      source  = "carlpett/sops"
      version = "~> 1.1"
    }
  }
}

# Credentials come from VERDA_CLIENT_ID / VERDA_CLIENT_SECRET — export them from
# the Verda CLI's credentials file with:  eval "$(../../../scripts/verda-env.sh)"
provider "verda" {}

# Zone:DNS:Edit token for the gateway A records, from the same SOPS file the
# other clusters read (cf.api-key).
data "sops_file" "config" {
  source_file = "${path.module}/../../../config/config.enc.yaml"
}

provider "cloudflare" {
  api_token = data.sops_file.config.data["cf.api-key"]
}
