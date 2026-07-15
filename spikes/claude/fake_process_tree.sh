#!/bin/sh
set -eu

: "${DISPATCH_CLAUDE_PROCESS_FILE:?set DISPATCH_CLAUDE_PROCESS_FILE}"

child=""
cleanup() {
  trap - INT TERM EXIT
  if [ -n "$child" ]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  exit 0
}
trap cleanup INT TERM EXIT

sleep 300 &
child=$!
printf '%s %s\n' "$$" "$child" > "$DISPATCH_CLAUDE_PROCESS_FILE"
wait "$child"
