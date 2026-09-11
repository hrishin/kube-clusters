#!/bin/bash
# Runs on a node (via remote-exec) and blocks until the startup script has
# finished building it with image-builder — or fails fast if that build failed.
set -uo pipefail

MARKER_DIR=/var/lib/image-builder
LOG=/var/log/verda-node-build.log
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-2400}"

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while :; do
  if [ -f "$MARKER_DIR/verda-node.done" ]; then
    echo "node build complete"
    exit 0
  fi
  if [ -f "$MARKER_DIR/verda-node.failed" ]; then
    echo "node build FAILED; last 60 lines of $LOG:"
    tail -n 60 "$LOG" 2>/dev/null || true
    exit 1
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "timed out after ${TIMEOUT_SECONDS}s waiting for node build; last 60 lines of $LOG:"
    tail -n 60 "$LOG" 2>/dev/null || true
    exit 1
  fi
  sleep 15
done
