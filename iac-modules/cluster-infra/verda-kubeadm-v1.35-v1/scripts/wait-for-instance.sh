#!/usr/bin/env bash
# Terraform `external` data source: polls the Verda API until an instance is
# `running` with a public IP, then returns {ip, private_ip, status}.
#
# The verda Terraform provider returns from Create as soon as the order is
# accepted and never exposes private_ip, hence this script. Uses the same
# VERDA_CLIENT_ID / VERDA_CLIENT_SECRET the provider reads.
set -euo pipefail

eval "$(jq -r '@sh "INSTANCE_ID=\(.instance_id) TIMEOUT=\(.timeout // "900")"')"

: "${VERDA_CLIENT_ID:?VERDA_CLIENT_ID is not set (see scripts/verda-env.sh)}"
: "${VERDA_CLIENT_SECRET:?VERDA_CLIENT_SECRET is not set (see scripts/verda-env.sh)}"
BASE_URL="${VERDA_BASE_URL:-https://api.verda.com/v1}"

get_token() {
  curl -fsS -X POST "$BASE_URL/oauth2/token" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg id "$VERDA_CLIENT_ID" --arg secret "$VERDA_CLIENT_SECRET" \
          '{grant_type: "client_credentials", client_id: $id, client_secret: $secret}')" \
    | jq -r '.access_token'
}

token="$(get_token)"
deadline=$(( $(date +%s) + TIMEOUT ))
last=""
while :; do
  if resp="$(curl -sS -w '\n%{http_code}' "$BASE_URL/instances/$INSTANCE_ID" -H "Authorization: Bearer $token")"; then
    code="${resp##*$'\n'}"
    body="${resp%$'\n'*}"
    if [ "$code" = "401" ]; then
      token="$(get_token)"
    elif [ "$code" = "200" ]; then
      status="$(jq -r '.status // ""' <<<"$body")"
      ip="$(jq -r '.ip // ""' <<<"$body")"
      private_ip="$(jq -r '.private_ip // ""' <<<"$body")"
      case "$status" in
        running)
          if [ -n "$ip" ]; then
            jq -n --arg ip "$ip" --arg private_ip "$private_ip" --arg status "$status" \
              '{ip: $ip, private_ip: $private_ip, status: $status}'
            exit 0
          fi
          ;;
        error|installation_failed|no_capacity|discontinued|notfound|deleting)
          echo "instance $INSTANCE_ID entered terminal status '$status'" >&2
          exit 1
          ;;
      esac
      last="$status"
    else
      echo "unexpected HTTP $code from Verda API: $body" >&2
    fi
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "timed out after ${TIMEOUT}s waiting for instance $INSTANCE_ID (last status: '$last')" >&2
    exit 1
  fi
  sleep 10
done
