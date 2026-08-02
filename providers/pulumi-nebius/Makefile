PROVIDER_VERSION ?= 0.6.35
PROVIDER_NAME    := nebius
TFGEN_BIN        := pulumi-tfgen-$(PROVIDER_NAME)
RESOURCE_BIN     := pulumi-resource-$(PROVIDER_NAME)
PLUGIN_DIR       := $(HOME)/.pulumi/plugins/resource-$(PROVIDER_NAME)-v$(PROVIDER_VERSION)

.DEFAULT_GOAL := build

# ── Build ─────────────────────────────────────────────────────────────────────
# Produces two binaries:
#   bin/pulumi-tfgen-nebius       — schema generator (dev-time)
#   bin/pulumi-resource-nebius    — provider binary  (runtime, called by pulumi up)

.PHONY: build
build: bin/$(TFGEN_BIN) bin/$(RESOURCE_BIN)

bin/$(TFGEN_BIN):
	go build -trimpath -o $@ ./cmd/$(TFGEN_BIN)

bin/$(RESOURCE_BIN):
	go build -trimpath -o $@ ./cmd/$(RESOURCE_BIN)

# ── Schema generation ─────────────────────────────────────────────────────────
# Runs pulumi-tfgen-nebius to introspect the Terraform provider and write
# sdk/schema.json. Must run before `make sdk/python`.

.PHONY: generate
generate: bin/$(TFGEN_BIN)
	./bin/$(TFGEN_BIN) all --out sdk/
	cp sdk/schema.json schema.json

# ── Python SDK ────────────────────────────────────────────────────────────────
# Generates pulumi_nebius Python package from schema.json.

.PHONY: sdk/python
sdk/python: schema.json
	pulumi package gen-sdk --language python schema.json --out sdk/python

# ── Install provider binary into ~/.pulumi/plugins/ ──────────────────────────
# This is what `pulumi up` resolves when it looks for resource-nebius-vX.Y.Z.
# Run once after `make build`, or after every `make bump`.

.PHONY: install
install: bin/$(RESOURCE_BIN)
	mkdir -p $(PLUGIN_DIR)
	cp bin/$(RESOURCE_BIN) $(PLUGIN_DIR)/$(RESOURCE_BIN)
	@echo "Installed: $(PLUGIN_DIR)/$(RESOURCE_BIN)"

# ── Full pipeline (generate → build → install → sdk/python) ──────────────────

.PHONY: all
all: generate build install sdk/python
	pip install -e sdk/python
	@echo "Done. pulumi_nebius $(PROVIDER_VERSION) ready."

# ── Version bump ─────────────────────────────────────────────────────────────
# Usage: make bump VERSION=0.6.36
# Updates Makefile pin, go.mod dep, and schema.json version string.

.PHONY: bump
bump:
	@[ -n "$(VERSION)" ] || (echo "Usage: make bump VERSION=x.y.z"; exit 1)
	sed -i 's/^PROVIDER_VERSION ?= .*/PROVIDER_VERSION ?= $(VERSION)/' Makefile
	go get github.com/nebius/terraform-provider-nebius@v$(VERSION)
	go mod tidy
	@if [ -f schema.json ]; then \
	  python3 -c "import json,sys; d=json.load(open('schema.json')); d['version']='$(VERSION)'; json.dump(d,open('schema.json','w'),indent=2)"; \
	fi
	@sed -i 's/__version__ = ".*"/__version__ = "$(VERSION)"/' \
	  sdk/python/pulumi_nebius/__init__.py

# ── Lint / test ───────────────────────────────────────────────────────────────

.PHONY: lint
lint:
	go vet ./...

.PHONY: test
test:
	go test ./...

.PHONY: clean
clean:
	rm -rf bin/ sdk/python/build sdk/python/*.egg-info
