PROVIDER_VERSION ?= 0.6.37
PROVIDER_NAME    := nebius
TF_REGISTRY      := registry.terraform.io/nebius/nebius
PYTHON           := python3

# Scratch project used to drive `pulumi package add`
SCRATCH_DIR := /tmp/pulumi-nebius-scratch

.DEFAULT_GOAL := sdk

# ── Generate Python SDK ────────────────────────────────────────────────────────
# Downloads the Terraform provider binary at runtime via pulumi-terraform-provider
# (no Go compilation required) and generates sdk/ from its embedded schema.
#
# Usage:  make sdk              — regenerate from current PROVIDER_VERSION
#         make sdk VERSION=x.y.z

.PHONY: sdk
sdk:
	@[ -n "$(VERSION)" ] && $(MAKE) _bump VERSION=$(VERSION) || true
	rm -rf $(SCRATCH_DIR)
	mkdir -p $(SCRATCH_DIR)
	printf 'name: nebius-scratch\nruntime: python\n' > $(SCRATCH_DIR)/Pulumi.yaml
	cd $(SCRATCH_DIR) && pulumi package add terraform-provider \
	    $(TF_REGISTRY) $(PROVIDER_VERSION) 2>&1 \
	    | grep -v "^warning:" | grep -v "^error: linking package" || true
	rm -rf sdk
	cp -r $(SCRATCH_DIR)/sdks/nebius sdk
	rm -rf $(SCRATCH_DIR)
	@echo "SDK generated: sdk/pulumi_nebius/  (version $(PROVIDER_VERSION))"

# ── Install Python SDK into the active venv ────────────────────────────────────

.PHONY: install
install: sdk
	$(PYTHON) -m pip install -e sdk
	@echo "pulumi_nebius $(PROVIDER_VERSION) installed."

# ── Full pipeline ──────────────────────────────────────────────────────────────

.PHONY: all
all: install
	@echo "Done. pulumi_nebius $(PROVIDER_VERSION) ready."

# ── Version bump (internal) ───────────────────────────────────────────────────
# Updates Makefile pin and pyproject.toml version string.

.PHONY: _bump
_bump:
	@[ -n "$(VERSION)" ] || (echo "Usage: make sdk VERSION=x.y.z"; exit 1)
	sed -i 's/^PROVIDER_VERSION ?= .*/PROVIDER_VERSION ?= $(VERSION)/' Makefile

# ── Convenience target for CI ─────────────────────────────────────────────────

.PHONY: bump
bump:
	@[ -n "$(VERSION)" ] || (echo "Usage: make bump VERSION=x.y.z"; exit 1)
	$(MAKE) _bump VERSION=$(VERSION)
	@echo "Version pinned to $(VERSION). Run 'make sdk' to regenerate."
