#!/usr/bin/env bash
# Export Verda API credentials for Terraform from the Verda CLI's credentials
# file (AWS-style INI written by `verda auth login`), so the CLI stays the
# single place credentials live.
#
#   eval "$(scripts/verda-env.sh)"                  # [default] profile
#   eval "$(scripts/verda-env.sh k8s-gpu-cluster)"  # a named profile
#
# Verda API keys are scoped to a console project; the profile you export must
# hold a key created in the project the cluster should land in
# (clusters/verda-alpha/config.yaml: verda_project). To add one:
#   verda auth login --profile <project> --client-id ... --client-secret ...
set -euo pipefail

profile="${1:-default}"
file="${VERDA_SHARED_CREDENTIALS_FILE:-$HOME/.verda/credentials}"

if [ ! -f "$file" ]; then
  echo "verda credentials file not found: $file (run 'verda auth login')" >&2
  exit 1
fi

# Print `key=value` pairs from the requested INI section only.
section="$(awk -v want="[$profile]" '
  /^\[/ { in_section = ($0 == want); next }
  in_section && /^[[:space:]]*verda_/ { gsub(/[[:space:]]/, ""); print }
' "$file")"

if [ -z "$section" ]; then
  echo "profile [$profile] not found in $file" >&2
  exit 1
fi

client_id="$(printf '%s\n' "$section" | awk -F= '$1=="verda_client_id"{print $2}')"
client_secret="$(printf '%s\n' "$section" | awk -F= '$1=="verda_client_secret"{print $2}')"
base_url="$(printf '%s\n' "$section" | awk -F= '$1=="verda_base_url"{print $2}')"

if [ -z "$client_id" ] || [ -z "$client_secret" ]; then
  echo "profile [$profile] is missing verda_client_id / verda_client_secret" >&2
  exit 1
fi

printf 'export VERDA_CLIENT_ID=%q\n' "$client_id"
printf 'export VERDA_CLIENT_SECRET=%q\n' "$client_secret"
[ -n "$base_url" ] && printf 'export VERDA_BASE_URL=%q\n' "$base_url"
printf 'export VERDA_PROFILE=%q\n' "$profile"
