# Claude control-plane probes

These probes support
[`claude-control-plane-verification.md`](../../docs/research/claude-control-plane-verification.md).
They are research fixtures, not a production Claude adapter.

`hook_capture.sh` is a content-minimizing command hook. It records only event
names, session/prompt IDs, bounded lifecycle fields, input key names, and
synthetic `DISPATCH-PROBE:<id>` markers. It drops prompts, transcript paths,
working directories, tool input/output, and model output.

`hook_fail.sh` is a silent exit-1 fixture. `hook_timeout.sh` is a silent
two-second delay fixture used with a one-second timeout. `hook_block_prompt.sh`
is a sibling blocking hook; `hook_continue_once.sh` continues exactly one Stop
cycle; and `hook_preflight.sh` returns a caller-supplied generation nonce before
message submission. `fake_repl.sh` emits
synthetic target-owned acceptance/completion markers for safe zmx experiments;
those markers characterize zmx only and never substitute for Claude hooks.

`fixtures/receipt-sequence.jsonl` and `fixtures/aggregate-receipts.jsonl` are
content-free observed schema/structural sequences.
UUIDs and markers are synthetic; `keys` preserves the installed input shape
without retaining content-bearing values.

`sanitize_stream.jq` drops content while Claude is running. `assert_probe.py`
checks receipt, blocked-prompt, continued-Stop, fail-open, timeout, preflight,
interrupt, duplicate, and replay cases. The static replay/Stop distinction is:

```bash
uv run python spikes/claude/assert_probe.py aggregate-fixture \
  spikes/claude/fixtures/aggregate-receipts.jsonl
uv run python spikes/claude/assert_probe.py negative-fixtures \
  spikes/claude/fixtures/negative
jq -cf spikes/claude/sanitize_stream.jq \
  spikes/claude/fixtures/whitespace-block-raw.jsonl |
  jq -e '.blocking_decision == true and (has("stdout") | not)'
```

## Safety envelope

- Use Claude Code 2.1.210 and zmx 0.6.0 for these exact claims.
- Use Haiku, minimal prompts, an explicit UUID, and a temporary Git repository.
- Never target an existing session or existing Agent View short ID.
- Never inspect transcripts, auth, or zmx history for real Claude prompts.
- Record settings hashes/metadata without printing settings contents.
- Use a unique prefix and remove every Agent View/zmx entry created.
- A successful process or zmx exit is not a receipt.

From the repository root, create the isolated workspace and generate an exact
mode-0600 settings file instead of reconstructing hook syntax:

```bash
spike_root="$PWD/spikes/claude"
repo="$(mktemp -d -t dispatch-claude-probe.XXXXXX)"
git -C "$repo" init -q
export DISPATCH_CLAUDE_PROBE_LOG="$repo/hooks.jsonl"
settings="$repo/dispatch-claude-settings.json"
"$spike_root/make_settings.sh" "$settings" receipt
trap 'rm -rf "$repo"' EXIT
set -o pipefail
```

The examples use Bash's `PIPESTATUS`; run these blocks in Bash.

Available modes are `receipt`, `block-prompt`, `continue-stop`, `fail`, `timeout`,
and `preflight`. The capture modes require `DISPATCH_CLAUDE_PROBE_LOG` inside the
temporary repo. `continue-stop` additionally requires
`DISPATCH_CLAUDE_STOP_STATE` there. Generate preflight settings with a synthetic
nonce in the environment; never store or print other hook stdout.

## Minimal receipt/resume probe

Create a temp repository and use `make_settings.sh ... receipt` to register
`hook_capture.sh` for `SessionStart`, `UserPromptSubmit`, `Stop`, `StopFailure`,
and `SessionEnd`.
Point `DISPATCH_CLAUDE_PROBE_LOG` inside the temp directory, then run:

```bash
session_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"

set +e
jq -cn --arg prompt 'DISPATCH-PROBE:first Reply only OK.' \
  '{type:"user",message:{role:"user",content:$prompt}}' |
  claude --print --verbose \
    --input-format stream-json \
    --output-format stream-json \
    --include-hook-events \
    --replay-user-messages \
    --session-id "$session_id" \
    --settings "$settings" \
    --model haiku \
    --max-turns 1 |
  jq -cf "$spike_root/sanitize_stream.jq" \
    > "$repo/first.structure.jsonl"
first_status=${PIPESTATUS[1]}
jq -cn --argjson code "$first_status" \
  '{sequence:999999,type:"process_exit",exit_code:$code}' \
  >> "$repo/first.structure.jsonl"

claude --resume "$session_id" --print \
  'DISPATCH-PROBE:second Reply only OK.' \
  --verbose \
  --output-format stream-json \
  --include-hook-events \
  --settings "$settings" \
  --model haiku \
  --max-turns 1 |
  jq -cf "$spike_root/sanitize_stream.jq" \
    > "$repo/second.structure.jsonl"
second_status=${PIPESTATUS[0]}
jq -cn --argjson code "$second_status" \
  '{sequence:999999,type:"process_exit",exit_code:$code}' \
  >> "$repo/second.structure.jsonl"
set -e

uv run python "$spike_root/assert_probe.py" receipt \
  "$repo/first.structure.jsonl"
uv run python "$spike_root/assert_probe.py" receipt \
  "$repo/second.structure.jsonl"
```

Reduce stdout immediately to type/subtype/session ID, hook name/outcome, and
error state. Do not save assistant/user content. Verify the sanitized hook log
has the selected UUID, both markers, and matching prompt IDs. This proves
correlation only. Processing additionally requires every `UserPromptSubmit` hook
response to reach a terminal non-blocking outcome and owned-stream assistant/tool
activity.
Completion requires the final Stop hook set to settle without continuation,
terminal result success, and clean process exit.

## Hook aggregation and preflight probes

Configure `hook_capture.sh` and `hook_block_prompt.sh` as sibling
`UserPromptSubmit` hooks. Claude Code 2.1.210 ran the observer, then the blocker
exited 2; there was no assistant activity or Stop even though the terminal result
subtype said success. This is the negative acceptance fixture.

Configure `hook_capture.sh` and `hook_continue_once.sh` as sibling `Stop` hooks,
with `DISPATCH_CLAUDE_STOP_STATE` pointing inside the temporary directory. The
same prompt ID produced two Stop occurrences separated by assistant activity.
This is the negative single-Stop completion fixture.

Run the aggregate and fail-open cases with the same sanitized pipeline:

```bash
for mode in block-prompt continue-stop fail timeout; do
  : > "$DISPATCH_CLAUDE_PROBE_LOG"
  settings="$repo/$mode.settings.json"
  structure="$repo/$mode.structure.jsonl"
  if [ "$mode" = continue-stop ]; then
    export DISPATCH_CLAUDE_STOP_STATE="$repo/continue-once"
  fi
  "$spike_root/make_settings.sh" "$settings" "$mode"
  case $mode in
    block-prompt) prompt='DISPATCH-PROBE:block Reply only OK.' ;;
    continue-stop) prompt='DISPATCH-PROBE:continue Reply, then continue once.' ;;
    fail) prompt='DISPATCH-PROBE:fail Reply only OK.' ;;
    timeout) prompt='DISPATCH-PROBE:timeout Reply only OK.' ;;
  esac
  set +e
  jq -cn --arg prompt "$prompt" \
    '{type:"user",message:{role:"user",content:$prompt}}' |
    claude --print --verbose \
      --input-format stream-json --output-format stream-json \
      --include-hook-events \
      --session-id "$(uuidgen | tr '[:upper:]' '[:lower:]')" \
      --settings "$settings" --model haiku --max-turns 2 |
    jq -cf "$spike_root/sanitize_stream.jq" > "$structure"
  claude_status=${PIPESTATUS[1]}
  jq -cn --argjson code "$claude_status" \
    '{sequence:999999,type:"process_exit",exit_code:$code}' >> "$structure"
  set -e
  uv run python "$spike_root/assert_probe.py" "$mode" "$structure"
done
```

For a no-message preflight, register `hook_preflight.sh` on `SessionStart`, then:

```bash
session_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"
preflight_settings="$repo/preflight.settings.json"
export DISPATCH_CLAUDE_PREFLIGHT_NONCE="preflight-$session_id"
"$spike_root/make_settings.sh" "$preflight_settings" preflight
set +e
claude --print --verbose \
  --input-format stream-json \
  --output-format stream-json \
  --include-hook-events \
  --session-id "$session_id" \
  --settings "$preflight_settings" \
  --model haiku </dev/null |
  jq -cf "$spike_root/sanitize_stream.jq" \
    > "$repo/preflight.structure.jsonl"
preflight_status=${PIPESTATUS[0]}
jq -cn --argjson code "$preflight_status" \
  '{sequence:999999,type:"process_exit",exit_code:$code}' \
  >> "$repo/preflight.structure.jsonl"
set -e
uv run python "$spike_root/assert_probe.py" preflight \
  "$repo/preflight.structure.jsonl"
```

Require one successful response carrying the current nonce before writing any
prompt frame. Do not persist other hook stdout: sibling SessionStart hooks may
return content. A missing nonce is safe failure-before-submission. After any
possible stdin write, loss is indeterminate and must never auto-retry.

## Interrupt, duplicate, and hook-failure probes

For interrupt, resume with a synthetic prompt that invokes `sleep 30`, wait for
processing evidence, send SIGINT to the exact owned process, and require exit
130 with no matching `Stop`. A later fresh `--resume` must complete, but the
provider completion of the interrupted attempt remains unknown.

For concurrency/duplicate behavior, start two resume processes against the same
disposable UUID with distinct markers, then repeat one marker. Claude 2.1.210
processed both and assigned distinct prompt IDs. Production must serialize and
dedupe before spawn.

After creating one disposable `$session_id`, prove the duplicate behavior without
retaining output content:

```bash
: > "$DISPATCH_CLAUDE_PROBE_LOG"
settings="$repo/duplicate.settings.json"
"$spike_root/make_settings.sh" "$settings" receipt
duplicate_pids=""
for copy in 1 2; do
  (
    structure="$repo/duplicate-$copy.structure.jsonl"
    set +e
    claude --resume "$session_id" --print \
      'DISPATCH-PROBE:duplicate Reply only OK.' \
      --verbose --output-format stream-json --include-hook-events \
      --settings "$settings" --model haiku --max-turns 1 |
      jq -cf "$spike_root/sanitize_stream.jq" > "$structure"
    status=${PIPESTATUS[0]}
    jq -cn --argjson code "$status" \
      '{sequence:999999,type:"process_exit",exit_code:$code}' >> "$structure"
    set -e
    exit "$status"
  ) &
  duplicate_pids="$duplicate_pids $!"
done
for pid in $duplicate_pids; do wait "$pid"; done
uv run python "$spike_root/assert_probe.py" duplicate \
  "$DISPATCH_CLAUDE_PROBE_LOG" \
  "$repo/duplicate-1.structure.jsonl" "$repo/duplicate-2.structure.jsonl"
```

Add `hook_fail.sh` beside the capture hook on `UserPromptSubmit`. The turn should
complete while stream output reports exit 1 for the failing hook. Repeat with
`hook_timeout.sh` configured with a one-second timeout; 2.1.210 reported
`outcome=cancelled`, exit 1, and still completed. Neither failure is rejection.

For interrupt, use process substitution so raw output is reduced in flight while
`$!` remains the owned Claude pid:

```bash
: > "$DISPATCH_CLAUDE_PROBE_LOG"
settings="$repo/interrupt.settings.json"
"$spike_root/make_settings.sh" "$settings" receipt
claude --resume "$session_id" --print \
  'DISPATCH-PROBE:interrupt Use Bash to run sleep 30.' \
  --verbose --output-format stream-json --include-hook-events \
  --settings "$settings" --model haiku --permission-mode bypassPermissions \
  > >(jq -cf "$spike_root/sanitize_stream.jq" \
      > "$repo/interrupt.structure.jsonl") &
claude_pid=$!
for _ in $(seq 1 100); do
  jq -e 'select(.hook_event_name == "PreToolUse")' \
    "$DISPATCH_CLAUDE_PROBE_LOG" >/dev/null && break
  sleep 0.1
done
kill -INT "$claude_pid"
set +e
wait "$claude_pid"
code=$?
set -e
test "$code" -eq 130
jq -cn --argjson code "$code" \
  '{sequence:999999,type:"process_exit",exit_code:$code}' \
  >> "$repo/interrupt.structure.jsonl"
uv run python "$spike_root/assert_probe.py" interrupt \
  "$repo/interrupt.structure.jsonl"
```

## Agent View probe

Start a uniquely named background session:

```bash
claude --bg \
  --name "$unique_name" \
  --settings "$settings" \
  --model haiku \
  --permission-mode default \
  'DISPATCH-PROBE:attention Use AskUserQuestion to ask A or B, then wait.'
```

Reduce `claude agents --json --cwd "$repo"` to id/sessionId/name/state/status/
waitingFor/kind and cwd equality. The observed session reached blocked/waiting,
with `PermissionRequest` and `Notification(permission_prompt)`. Use
`claude attach SHORT_ID` only for that disposable session, answer, detach with
Ctrl-Z, and send a second synthetic prompt if testing the human path.

Cleanup is mandatory:

```bash
claude stop "$short_id"
claude rm "$short_id"
claude agents --json --all --cwd "$repo"
```

The filtered result must contain no created ID. `rm` removes the Agent View
entry/worktree but intentionally leaves Claude's resumable transcript.

## Isolated zmx fake-target probe

Never send a real Claude prompt through zmx 0.6.0 in this probe: that version
logs PTY input bytes. Use a unique `ZMX_DIR`, `ZMX_DIR_MODE=0700`,
`ZMX_LOG_MODE=0600`, and `fake_repl.sh` only.

The verified negative cases were simultaneous raw sends arriving in a different
order, Ctrl-C send success without target interruption, and post-kill send exit
zero despite an unresponsive-session error. Finish with `zmx kill` and require
the isolated `zmx list --short` count to be zero.

The exact fake-only sequence is:

```bash
export ZMX_DIR="$repo/zmx"
export ZMX_DIR_MODE=0700
export ZMX_LOG_MODE=0600
zmx_name="dispatch-probe-$$"
zmx attach "$zmx_name" "$spike_root/fake_repl.sh" \
  </dev/null >/dev/null 2>&1 &
zmx_client=$!
for _ in $(seq 1 50); do
  zmx list --short | rg -qx "$zmx_name" && break
  sleep 0.1
done
(printf 'a\n' | zmx send "$zmx_name") &
send_a=$!
(printf 'b\n' | zmx send "$zmx_name") &
send_b=$!
wait "$send_a" "$send_b"
printf 'c\n' | zmx send "$zmx_name"
zmx send "$zmx_name" "$(printf '\003')"
sleep 2
zmx history "$zmx_name" | rg -q 'completed:c'
zmx kill "$zmx_name"
wait "$zmx_client" 2>/dev/null || true
set +e
lost_output=$(printf 'lost\n' | zmx send "$zmx_name" 2>&1)
lost_status=$?
set -e
test "$lost_status" -eq 0
printf '%s\n' "$lost_output" | rg -qi 'unresponsive|not found'
test "$(zmx list --short | rg -c "^$zmx_name$" || true)" -eq 0
```

## Process-group fixture

Run `uv run python spikes/claude/process_group_probe.py`. It starts
`fake_process_tree.sh` in a new POSIX session, verifies the recorded parent and
child share the expected pgid, sends SIGINT to that group, escalates to TERM if
needed, and asserts both processes exit. This proves the supervision primitive;
the implementation's opt-in live scenario must repeat the descendant check with
a disposable Claude tool turn.
