#!/usr/bin/env bash
# cf-dns.sh — upsert the cluster A record in Cloudflare
#
# Creates (or updates) an A record so that:
#   cluster1.eu-north-1.kube.nebius.hrishi.dev → 89.169.102.151
#
# Usage:
#   ./cf-dns.sh <CLOUDFLARE_API_TOKEN>
#
# The token needs Zone:DNS:Edit on the hrishi.dev zone.
#
# Nebius DNS v1 only supports VPC-private zones via API; public DNS is
# managed here in Cloudflare directly.

set -euo pipefail

CF_TOKEN="${1:?Usage: $0 <CF_API_TOKEN>}"

CF_ZONE_NAME="hrishi.dev"
RECORD_NAME="cluster1.eu-north-1.kube.nebius"   # relative; Cloudflare appends .hrishi.dev
LB_IP="89.169.102.151"
TTL=300

CF_API="https://api.cloudflare.com/client/v4"

# ── Helpers ────────────────────────────────────────────────────────────────

cf() {
  curl -sf -H "Authorization: Bearer $CF_TOKEN" \
       -H "Content-Type: application/json" \
       "$@"
}

check() {
  local resp="$1"
  if ! echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('success') else 1)" 2>/dev/null; then
    echo "Cloudflare API error: $resp" >&2
    exit 1
  fi
}

# ── Zone ID ────────────────────────────────────────────────────────────────

echo "Looking up zone $CF_ZONE_NAME ..."
ZONE_RESP=$(cf "$CF_API/zones?name=$CF_ZONE_NAME")
check "$ZONE_RESP"
CF_ZONE_ID=$(echo "$ZONE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['id'])")
echo "Zone ID: $CF_ZONE_ID"

# ── Upsert A record ────────────────────────────────────────────────────────

FQDN="${RECORD_NAME}.${CF_ZONE_NAME}"
echo "Checking for existing A record for $FQDN ..."
LIST_RESP=$(cf "$CF_API/zones/$CF_ZONE_ID/dns_records?type=A&name=$FQDN")
check "$LIST_RESP"
EXISTING_ID=$(echo "$LIST_RESP" | python3 -c "
import sys,json
items = json.load(sys.stdin)['result']
print(items[0]['id'] if items else '')
")

RECORD_BODY=$(python3 -c "
import json
print(json.dumps({'type':'A','name':'$RECORD_NAME','content':'$LB_IP','ttl':$TTL,'proxied':False}))
")

if [ -n "$EXISTING_ID" ]; then
  echo "Updating existing record $EXISTING_ID ..."
  RESP=$(cf -X PUT "$CF_API/zones/$CF_ZONE_ID/dns_records/$EXISTING_ID" -d "$RECORD_BODY")
else
  echo "Creating new A record ..."
  RESP=$(cf -X POST "$CF_API/zones/$CF_ZONE_ID/dns_records" -d "$RECORD_BODY")
fi
check "$RESP"

echo "Done. A record:"
echo "  $FQDN → $LB_IP (TTL $TTL)"
echo ""
echo "Verify:"
echo "  dig +short $FQDN @1.1.1.1"
