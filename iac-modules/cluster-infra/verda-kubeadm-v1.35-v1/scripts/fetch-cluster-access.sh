#!/usr/bin/env bash
# Terraform `external` data source: pulls the kubeadm admin kubeconfig and the
# CA public-key hash (for `kubeadm join --discovery-token-ca-cert-hash`) from
# the control plane over SSH.
set -euo pipefail

eval "$(jq -r '@sh "HOST=\(.host) USER=\(.user) KEY=\(.private_key_path)"')"

ssh_cp() {
  ssh -i "$KEY" \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
      -o ConnectTimeout=20 -o BatchMode=yes \
      "$USER@$HOST" "$@"
}

kubeconfig_b64="$(ssh_cp 'base64 -w0 /etc/kubernetes/admin.conf')"
# Same derivation kubeadm documents: sha256 over the DER-encoded SubjectPublicKeyInfo of the CA cert.
ca_cert_hash="$(ssh_cp "openssl x509 -pubkey -noout -in /etc/kubernetes/pki/ca.crt | openssl pkey -pubin -outform der | openssl dgst -sha256 -hex | sed 's/^.* //'")"

jq -n --arg k "$kubeconfig_b64" --arg h "$ca_cert_hash" '{kubeconfig_b64: $k, ca_cert_hash: $h}'
