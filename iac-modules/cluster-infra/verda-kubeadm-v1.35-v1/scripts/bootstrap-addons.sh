#!/bin/bash
# Runs on the control plane. Installs Cilium and Flux with the release names the
# cluster's Flux extensions use, then hands over to Flux (GitRepository +
# Kustomization). Idempotent — re-run on any values/manifest change.
set -euo pipefail

: "${CILIUM_VERSION:?}"
: "${FLUX_CHART_VERSION:?}"
export KUBECONFIG=/etc/kubernetes/admin.conf
cd /root/bootstrap

helm repo add cilium https://helm.cilium.io/ >/dev/null
helm repo add fluxcd-community https://fluxcd-community.github.io/helm-charts >/dev/null
helm repo update >/dev/null

echo "=== cilium ${CILIUM_VERSION}"
helm upgrade --install cilium cilium/cilium \
  --version "${CILIUM_VERSION}" \
  --namespace kube-system \
  --values cilium-values.yaml \
  --wait --timeout 15m

echo "=== waiting for nodes to be Ready"
kubectl wait --for=condition=Ready nodes --all --timeout=10m

echo "=== flux namespace, infra-outputs, secrets"
kubectl apply -f flux-bootstrap.yaml

echo "=== fluxcd ${FLUX_CHART_VERSION}"
helm upgrade --install fluxcd fluxcd-community/flux2 \
  --version "${FLUX_CHART_VERSION}" \
  --namespace flux-system \
  --values flux-values.yaml \
  --wait --timeout 10m

kubectl wait --for=condition=Established --timeout=2m \
  crd/gitrepositories.source.toolkit.fluxcd.io \
  crd/kustomizations.kustomize.toolkit.fluxcd.io

echo "=== flux GitRepository + root Kustomization"
kubectl apply -f flux-sync.yaml

# Secrets have been applied; don't leave plaintext copies on disk.
shred -u flux-bootstrap.yaml 2>/dev/null || rm -f flux-bootstrap.yaml
echo "=== bootstrap complete"
