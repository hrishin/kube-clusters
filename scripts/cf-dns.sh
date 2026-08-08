#!/usr/bin/env bash
# cf-dns.sh — upsert the cluster A record in Cloudflare
#
# Looks up the main-gateway LoadBalancer IP on the current kube context and
# creates (or updates) an A record so that:
#   <CLUSTER_NAME>.eu-north-1.kube.nebius.hrishi.dev → <LB_IP>
#
# Usage:
#   ./cf-dns.sh <CLUSTER_NAME> [CLOUDFLARE_API_TOKEN]
#
# If the token isn't passed as an argument, it's read from the
# CF_API_TOKEN environment variable.
#
# The token needs Zone:DNS:Edit on the hrishi.dev zone.
#
# Nebius DNS v1 only supports VPC-private zones via API; public DNS is
# managed here in Cloudflare directly.
#
# The LB service namespace/name can be overridden via LB_NAMESPACE /
# LB_SERVICE_NAME env vars; the kube context to query defaults to whatever
# is currently active and can be overridden via KUBE_CONTEXT.

set -euo pipefail

CLUSTER_NAME="${1:?Usage: $0 <CLUSTER_NAME> [CF_API_TOKEN] (or set CF_API_TOKEN env var)}"
CF_TOKEN="${2:-${CF_API_TOKEN:?Usage: $0 <CLUSTER_NAME> <CF_API_TOKEN> (or set CF_API_TOKEN env var)}}"

CF_ZONE_NAME="hrishi.dev"
RECORD_NAME="cluster1.eu-north-1.kube.nebius"   # relative; Cloudflare appends .hrishi.dev
TTL=300

LB_NAMESPACE="${LB_NAMESPACE:-kgateway-system}"
LB_SERVICE_NAME="${LB_SERVICE_NAME:-main-gateway}"
KUBE_CONTEXT="${KUBE_CONTEXT:-$(kubectl config current-context)}"

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

# ── LB IP lookup ───────────────────────────────────────────────────────────

echo "Looking up $LB_NAMESPACE/$LB_SERVICE_NAME LoadBalancer IP on context $KUBE_CONTEXT ..."
LB_IP=$(kubectl --context "$KUBE_CONTEXT" -n "$LB_NAMESPACE" get svc "$LB_SERVICE_NAME" \
          -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
if [ -z "$LB_IP" ]; then
  echo "No LoadBalancer IP found for $LB_NAMESPACE/$LB_SERVICE_NAME on context $KUBE_CONTEXT" >&2
  exit 1
fi
echo "LB IP: $LB_IP"

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
