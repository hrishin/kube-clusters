#!/usr/bin/env bash
# Watch ASG instance refresh progress for all node groups in a cluster.
# Usage: ./scripts/asg-refresh-watch.sh [cluster-name] [region] [interval-seconds]
#
# Loops until all active refreshes complete (or Ctrl-C).

set -euo pipefail

CLUSTER="${1:-infra-cluster}"
REGION="${2:-eu-west-2}"
INTERVAL="${3:-15}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

hr() { printf '%0.s─' {1..80}; echo; }

status_color() {
  case "$1" in
    Successful)          echo -n "$GREEN"  ;;
    InProgress|Pending)  echo -n "$YELLOW" ;;
    Cancelling|Cancelled|Failed|RollbackInProgress|RollbackFailed) echo -n "$RED" ;;
    *)                   echo -n "$DIM"    ;;
  esac
}

progress_bar() {
  local pct="${1:-0}"
  local width=30
  local filled=$(( pct * width / 100 ))
  local empty=$(( width - filled ))
  printf '['
  printf '%0.s█' $(seq 1 $filled 2>/dev/null) 2>/dev/null || true
  printf '%0.s░' $(seq 1 $empty  2>/dev/null) 2>/dev/null || true
  printf '] %3d%%' "$pct"
}

print_refresh() {
  local asg="$1"
  local short_name="${asg#"${CLUSTER}-"}"   # strip cluster prefix for readability

  local refresh
  refresh=$(aws autoscaling describe-instance-refreshes \
    --auto-scaling-group-name "$asg" \
    --region "$REGION" \
    --max-records 1 \
    --query "InstanceRefreshes[0]" \
    --output json 2>/dev/null)

  if [[ -z "$refresh" || "$refresh" == "null" ]]; then
    printf "  %-35s ${DIM}no refresh history${RESET}\n" "$short_name"
    return 0
  fi

  local status pct started reason instances_total instances_replaced
  status=$(echo "$refresh"             | jq -r '.Status // "Unknown"')
  pct=$(echo "$refresh"                | jq -r '.PercentageComplete // 0')
  started=$(echo "$refresh"            | jq -r '.StartTime // "—"')
  reason=$(echo "$refresh"             | jq -r '.StatusReason // ""')
  instances_total=$(echo "$refresh"    | jq -r '.InstancesToUpdate // "—"')
  instances_replaced=$(echo "$refresh" | jq -r '(.InstancesToUpdate - (.InstancesToUpdate - (.PercentageComplete * .InstancesToUpdate / 100 | floor))) // "—"' 2>/dev/null || echo "—")

  local color
  color=$(status_color "$status")

  printf "  ${BOLD}%-35s${RESET} ${color}%-20s${RESET} " "$short_name" "$status"
  progress_bar "$pct"
  printf "  ${DIM}started %s${RESET}\n" "${started:0:19}"

  if [[ -n "$reason" && "$status" != "Successful" ]]; then
    printf "  %-35s ${DIM}↳ %s${RESET}\n" "" "$reason"
  fi

  # return 1 if still active so the outer loop knows to keep polling
  case "$status" in
    InProgress|Pending|Cancelling|RollbackInProgress) return 1 ;;
  esac
  return 0
}

# ── Discover all ASGs for this cluster ────────────────────────────────────────
echo
printf "${BOLD}${CYAN}Discovering ASGs for cluster: ${CLUSTER}${RESET}\n"

ASGS=$(aws autoscaling describe-auto-scaling-groups \
  --region "$REGION" \
  --query "AutoScalingGroups[?contains(Tags[?Key=='eks:cluster-name'].Value[], '${CLUSTER}')].AutoScalingGroupName" \
  --output json | jq -r '.[]')

if [[ -z "$ASGS" ]]; then
  # Fallback: match by name prefix
  ASGS=$(aws autoscaling describe-auto-scaling-groups \
    --region "$REGION" \
    --query "AutoScalingGroups[?starts_with(AutoScalingGroupName, '${CLUSTER}')].AutoScalingGroupName" \
    --output json | jq -r '.[]')
fi

if [[ -z "$ASGS" ]]; then
  echo "No ASGs found for cluster '${CLUSTER}' in ${REGION}."
  exit 1
fi

ASG_COUNT=$(echo "$ASGS" | wc -l | tr -d ' ')
printf "${DIM}Found ${ASG_COUNT} ASG(s)${RESET}\n\n"

# ── Watch loop ────────────────────────────────────────────────────────────────
while true; do
  clear
  printf "${BOLD}${CYAN}ASG Instance Refresh — ${CLUSTER}${RESET}  ${DIM}($(date '+%H:%M:%S'), refreshing every ${INTERVAL}s)${RESET}\n"
  hr
  printf "  %-35s %-20s %-36s  %s\n" "NODE GROUP" "STATUS" "PROGRESS" "STARTED"
  hr

  all_done=true
  while IFS= read -r asg; do
    print_refresh "$asg" || all_done=false
  done <<< "$ASGS"

  hr
  if $all_done; then
    printf "${GREEN}All instance refreshes complete.${RESET}\n\n"
    break
  fi

  printf "${DIM}Next refresh in ${INTERVAL}s — Ctrl-C to exit${RESET}\n"
  sleep "$INTERVAL"
done
