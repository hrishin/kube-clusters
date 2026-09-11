#!/usr/bin/env bash
# Terraform `external` data source: returns the content of the file named by
# an environment variable ({content, path}); empty content if unset/missing.
set -euo pipefail

eval "$(jq -r '@sh "ENV_VAR=\(.env_var)"')"
path="${!ENV_VAR:-}"
content=""
if [ -n "$path" ] && [ -f "$path" ]; then
  content="$(cat "$path")"
fi
jq -n --arg content "$content" --arg path "$path" '{content: $content, path: $path}'
