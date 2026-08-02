PROVIDER_VERSION ?= 0.6.35
PROVIDER_NAME    := nebius
TFGEN            := pulumi-tfgen-$(PROVIDER_NAME)
PROVIDER_BIN     := pulumi-resource-$(PROVIDER_NAME)

.DEFAULT_GOAL := build

# ── Build ─────────────────────────────────────────────────────────────────────

.PHONY: build
build: provider sdk/python

.PHONY: provider
provider:
	go build -trimpath -o bin/$(PROVIDER_BIN) .

# ── Schema generation ─────────────────────────────────────────────────────────
# Runs the bridge generator to produce schema.json from the Terraform provider.
# Requires: pulumi-terraform-bridge and Go toolchain.

.PHONY: generate
generate: install_tfgen
	$(TFGEN) all --out sdk/
	cp sdk/schema.json schema.json

.PHONY: install_tfgen
install_tfgen:
	go install github.com/pulumi/pulumi-terraform-bridge/v3/cmd/$(TFGEN)@latest

# ── Python SDK ────────────────────────────────────────────────────────────────
# Generates the Python package from schema.json produced by `make generate`.

.PHONY: sdk/python
sdk/python: schema.json
	pulumi package gen-sdk --language python schema.json --out sdk/python
	cd sdk/python && pip install -e .

# ── Install into shared venv ──────────────────────────────────────────────────

.PHONY: install
install: sdk/python
	pip install -e sdk/python

# ── Lint / test ───────────────────────────────────────────────────────────────

.PHONY: lint
lint:
	go vet ./...
	cd sdk/python && python -m mypy pulumi_nebius --ignore-missing-imports

.PHONY: test
test:
	go test ./...

# ── Version bump ─────────────────────────────────────────────────────────────
# Usage: make bump VERSION=0.6.36

.PHONY: bump
bump:
	@[ -n "$(VERSION)" ] || (echo "Usage: make bump VERSION=x.y.z"; exit 1)
	sed -i 's/PROVIDER_VERSION ?= .*/PROVIDER_VERSION ?= $(VERSION)/' Makefile
	sed -i 's/nebius v.*/nebius v$(VERSION)/' go.mod
	sed -i 's/"version": ".*"/"version": "$(VERSION)"/' schema.json
	go mod tidy

.PHONY: clean
clean:
	rm -rf bin/ sdk/python/build sdk/python/*.egg-info
