#!/bin/sh
set -eu

: "${DISPATCH_CLAUDE_STOP_STATE:?set DISPATCH_CLAUDE_STOP_STATE}"

if mkdir "$DISPATCH_CLAUDE_STOP_STATE" 2>/dev/null; then
  printf '%s\n' '{"decision":"block","reason":"Continue once for receipt probe."}'
fi
