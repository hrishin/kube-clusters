terraform {
  required_version = ">= 1.6"

  required_providers {
    verda = {
      source  = "verda-cloud/verda"
      version = "~> 1.1"
    }
  }
}

# Credentials come from VERDA_CLIENT_ID / VERDA_CLIENT_SECRET — export them from
# the Verda CLI's credentials file with:  eval "$(../../../scripts/verda-env.sh)"
provider "verda" {}
