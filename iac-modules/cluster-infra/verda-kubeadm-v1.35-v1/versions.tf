terraform {
  required_version = ">= 1.6"

  required_providers {
    # https://registry.terraform.io/providers/verda-cloud/verda
    verda = {
      source  = "verda-cloud/verda"
      version = "~> 1.1"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    # Decrypts config/config.enc.yaml (GitHub token for the Flux GitRepository)
    # with the same age identity Flux/sops use everywhere else in this repo.
    sops = {
      source  = "carlpett/sops"
      version = "~> 1.1"
    }
  }
}
