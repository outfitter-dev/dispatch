#!/bin/sh
set -eu

: "${DISPATCH_CLAUDE_PREFLIGHT_NONCE:?set DISPATCH_CLAUDE_PREFLIGHT_NONCE}"

jq -cn --arg nonce "$DISPATCH_CLAUDE_PREFLIGHT_NONCE" \
  '{suppressOutput:true,_dispatch_preflight:{nonce:$nonce}}'
