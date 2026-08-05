#!/bin/sh
set -eu

: "${DISPATCH_CLAUDE_PROBE_LOG:?set DISPATCH_CLAUDE_PROBE_LOG}"

# Keep only routing/lifecycle facts. Prompt, transcript, tool input/output, cwd,
# and other content-bearing fields never reach the probe log.
jq -c '
  . as $event
  | {
      hook_event_name,
      session_id,
      prompt_id,
      source,
      reason,
      notification_type,
      tool_name,
      permission_mode,
      stop_hook_active,
      prompt_marker: (
        ($event.prompt // "")
        | if test("DISPATCH-PROBE:[A-Za-z0-9_-]+")
          then capture("DISPATCH-PROBE:(?<id>[A-Za-z0-9_-]+)").id
          else null
          end
      ),
      keys: ($event | keys | sort)
    }
  | with_entries(select(.value != null))
' >> "$DISPATCH_CLAUDE_PROBE_LOG"
