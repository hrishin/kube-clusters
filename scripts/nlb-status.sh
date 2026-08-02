#!/usr/bin/env bash
# Fetches the resource mapping and target health status for an AWS NLB.
# Usage: ./scripts/nlb-status.sh <nlb-arn> [region]

set -euo pipefail

NLB_ARN="${1:-arn:aws:elasticloadbalancing:eu-west-2:203070858830:loadbalancer/net/infra-cluster-nlb-ef1a66d/9f0d38c671736ad8}"
REGION="${2:-eu-west-2}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

hr() { printf '%0.s─' {1..70}; echo; }

# ── NLB overview ──────────────────────────────────────────────────────────────
echo
printf "${BOLD}${CYAN}NLB OVERVIEW${RESET}\n"
hr

NLB=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns "$NLB_ARN" \
  --region "$REGION" \
  --query 'LoadBalancers[0]' \
  --output json)

NAME=$(echo "$NLB"    | jq -r '.LoadBalancerName')
DNS=$(echo "$NLB"     | jq -r '.DNSName')
SCHEME=$(echo "$NLB"  | jq -r '.Scheme')
STATE=$(echo "$NLB"   | jq -r '.State.Code')
VPC=$(echo "$NLB"     | jq -r '.VpcId')
AZS=$(echo "$NLB"     | jq -r '[.AvailabilityZones[].ZoneName] | join(", ")')

STATE_COLOR="$GREEN"
[[ "$STATE" != "active" ]] && STATE_COLOR="$RED"

printf "  %-20s %s\n"  "Name:"    "$NAME"
printf "  %-20s %s\n"  "ARN:"     "$NLB_ARN"
printf "  %-20s %s\n"  "DNS:"     "$DNS"
printf "  %-20s %s\n"  "Scheme:"  "$SCHEME"
printf "  %-20s ${STATE_COLOR}%s${RESET}\n" "State:" "$STATE"
printf "  %-20s %s\n"  "VPC:"     "$VPC"
printf "  %-20s %s\n"  "AZs:"     "$AZS"

# ── Listeners ─────────────────────────────────────────────────────────────────
echo
printf "${BOLD}${CYAN}LISTENERS${RESET}\n"
hr

LISTENERS=$(aws elbv2 describe-listeners \
  --load-balancer-arn "$NLB_ARN" \
  --region "$REGION" \
  --query 'Listeners' \
  --output json)

echo "$LISTENERS" | jq -r '.[] | "\(.Port)/\(.Protocol)  →  \(.DefaultActions[0].TargetGroupArn)"' \
  | while IFS= read -r line; do
      printf "  %s\n" "$line"
    done

# ── Target groups + health ─────────────────────────────────────────────────────
TG_ARNS=$(echo "$LISTENERS" \
  | jq -r '.[].DefaultActions[0].TargetGroupArn' \
  | sort -u)

echo "$TG_ARNS" | while IFS= read -r TG_ARN; do
  echo
  printf "${BOLD}${CYAN}TARGET GROUP${RESET}\n"
  hr

  TG=$(aws elbv2 describe-target-groups \
    --target-group-arns "$TG_ARN" \
    --region "$REGION" \
    --query 'TargetGroups[0]' \
    --output json)

  TG_NAME=$(echo "$TG"     | jq -r '.TargetGroupName')
  TG_PROTO=$(echo "$TG"    | jq -r '.Protocol')
  TG_PORT=$(echo "$TG"     | jq -r '.Port')
  TG_TYPE=$(echo "$TG"     | jq -r '.TargetType')
  HC_PROTO=$(echo "$TG"    | jq -r '.HealthCheckProtocol')
  HC_PORT=$(echo "$TG"     | jq -r '.HealthCheckPort')
  HC_INTERVAL=$(echo "$TG" | jq -r '.HealthCheckIntervalSeconds')
  HC_HEALTHY=$(echo "$TG"  | jq -r '.HealthyThresholdCount')
  HC_UNHEALTHY=$(echo "$TG"| jq -r '.UnhealthyThresholdCount')

  printf "  %-22s %s\n"  "Name:"             "$TG_NAME"
  printf "  %-22s %s\n"  "ARN:"              "$TG_ARN"
  printf "  %-22s %s/%s\n" "Protocol/Port:"  "$TG_PROTO" "$TG_PORT"
  printf "  %-22s %s\n"  "Target type:"      "$TG_TYPE"
  printf "  %-22s %s:%s (every %ss, healthy=%s, unhealthy=%s)\n" \
    "Health check:" "$HC_PROTO" "$HC_PORT" "$HC_INTERVAL" "$HC_HEALTHY" "$HC_UNHEALTHY"

  # ── Targets + health ────────────────────────────────────────────────────────
  echo
  printf "${BOLD}  TARGETS${RESET}\n"
  printf "  %-22s %-16s %-12s %-36s %-16s %s\n" \
    "Instance ID" "Private IP" "AZ" "Node name (group)" "State" "Health"
  printf "  "; hr

  HEALTH=$(aws elbv2 describe-target-health \
    --target-group-arn "$TG_ARN" \
    --region "$REGION" \
    --query 'TargetHealthDescriptions' \
    --output json)

  INSTANCE_IDS=$(echo "$HEALTH" | jq -r '.[].Target.Id' | tr '\n' ' ')

  if [[ -z "${INSTANCE_IDS// }" ]]; then
    printf "  ${YELLOW}No targets registered.${RESET}\n"
    continue
  fi

  # Bulk-fetch instance metadata
  INSTANCES=$(aws ec2 describe-instances \
    --instance-ids $INSTANCE_IDS \
    --region "$REGION" \
    --query 'Reservations[].Instances[]' \
    --output json)

  echo "$HEALTH" | jq -c '.[]' | while IFS= read -r entry; do
    INSTANCE_ID=$(echo "$entry" | jq -r '.Target.Id')
    TARGET_PORT=$(echo "$entry" | jq -r '.Target.Port')
    HEALTH_STATE=$(echo "$entry" | jq -r '.TargetHealth.State')
    HEALTH_DESC=$(echo "$entry"  | jq -r '.TargetHealth.Description // ""')

    # Look up this instance in the bulk result
    INST=$(echo "$INSTANCES" \
      | jq -r --arg id "$INSTANCE_ID" '.[] | select(.InstanceId == $id)')

    PRIVATE_IP=$(echo "$INST" | jq -r '.PrivateIpAddress // "n/a"')
    AZ=$(echo "$INST"         | jq -r '.Placement.AvailabilityZone // "n/a"')
    NODE_NAME=$(echo "$INST"  \
      | jq -r '(.Tags // []) | map(select(.Key=="Name")) | .[0].Value // "n/a"')
    NODE_GROUP=$(echo "$INST" \
      | jq -r '(.Tags // []) | map(select(.Key=="NodeGroup")) | .[0].Value // ""')
    [[ -n "$NODE_GROUP" ]] && NODE_NAME="$NODE_NAME ($NODE_GROUP)"
    INST_STATE=$(echo "$INST" | jq -r '.State.Name // "n/a"')

    case "$HEALTH_STATE" in
      healthy)   COLOR="$GREEN"  ;;
      unhealthy) COLOR="$RED"    ;;
      *)         COLOR="$YELLOW" ;;
    esac

    HEALTH_LABEL="$HEALTH_STATE"
    [[ -n "$HEALTH_DESC" ]] && HEALTH_LABEL="$HEALTH_STATE ($HEALTH_DESC)"

    printf "  %-22s %-16s %-12s %-36s %-16s ${COLOR}%s${RESET}\n" \
      "$INSTANCE_ID" "$PRIVATE_IP" "$AZ" "$NODE_NAME" "$INST_STATE" "$HEALTH_LABEL"
  done
done

echo
