#!/usr/bin/env bash
# Configure Cloudflare NS delegation for the Nebius DNS zone.
#
# Usage:
#   ./cf-ns-delegate.sh <CF_API_TOKEN>
#
# What it does:
#   1. Looks up the Nebius DNS zone nameservers (via `nebius dns zone get`)
#   2. Creates NS records in Cloudflare for kube.nebius.hrishi.dev
#      pointing to those nameservers, delegating that subdomain to Nebius DNS
#
# Prerequisites:
#   - nebius CLI authenticated
#   - curl + python3

set -euo pipefail

CF_TOKEN="${1:?Usage: $0 <CF_API_TOKEN>}"
CF_API="https://api.cloudflare.com/client/v4"
CF_ZONE_NAME="hrishi.dev"
NS_RECORD_NAME="kube.nebius"   # → kube.nebius.hrishi.dev
NS_TTL=300

# ── 1. Get Nebius zone nameservers ──────────────────────────────────────────

ZONE_ID=$(pulumi stack output dns_zone_id 2>/dev/null || true)
if [ -z "$ZONE_ID" ]; then
  echo "ERROR: could not read dns_zone_id from Pulumi stack output." >&2
  echo "  Run: pulumi up  (in clusters/nebius-prod/infra/)" >&2
  exit 1
fi

echo "Fetching nameservers for Nebius zone $ZONE_ID ..."
NS_SERVERS=$(nebius dns zone get --id "$ZONE_ID" --format json | \
  python3 -c "
import json, sys
z = json.load(sys.stdin)
# nameservers may appear in status or spec depending on SDK version
ns = (z.get('status') or {}).get('nameServers') \
  or (z.get('spec') or {}).get('nameServers') \
  or []
for s in ns:
    print(s)
")

if [ -z "$NS_SERVERS" ]; then
  echo "ERROR: no nameservers returned for zone $ZONE_ID." >&2
  echo "  Check: nebius dns zone get --id $ZONE_ID" >&2
  exit 1
fi

echo "Nebius nameservers:"
echo "$NS_SERVERS"

# ── 2. Look up the Cloudflare zone ID for hrishi.dev ────────────────────────

echo ""
echo "Looking up Cloudflare zone for $CF_ZONE_NAME ..."
CF_ZONE_ID=$(curl -sf \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  "$CF_API/zones?name=$CF_ZONE_NAME" | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['result'][0]['id'])")

echo "Cloudflare zone ID: $CF_ZONE_ID"

# ── 3. Create NS records ─────────────────────────────────────────────────────

echo ""
echo "Creating NS records for $NS_RECORD_NAME.$CF_ZONE_NAME ..."
while IFS= read -r NS; do
  [ -z "$NS" ] && continue
  echo "  Adding NS → $NS"
  curl -sf -X POST \
    -H "Authorization: Bearer $CF_TOKEN" \
    -H "Content-Type: application/json" \
    "$CF_API/zones/$CF_ZONE_ID/dns_records" \
    -d "{\"type\":\"NS\",\"name\":\"$NS_RECORD_NAME\",\"content\":\"$NS\",\"ttl\":$NS_TTL}" | \
    python3 -c "
import json,sys
r=json.load(sys.stdin)
if r.get('success'):
    print('    OK — record id:', r['result']['id'])
else:
    # duplicate records are fine
    errs = r.get('errors',[])
    if any(e.get('code')==81057 for e in errs):
        print('    already exists, skipping')
    else:
        print('    ERROR:', errs, file=sys.stderr)
        sys.exit(1)
"
done <<< "$NS_SERVERS"

echo ""
echo "Done. Verify with:"
echo "  dig NS kube.nebius.hrishi.dev +short"
