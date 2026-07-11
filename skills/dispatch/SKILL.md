---
name: dispatch
description: Use when operating dispatch, creating or attaching Codex lanes, sending/steering/context-injecting/stopping through dispatch, checking provider capacity, managing triggers, checking daemon status/logs, or configuring the dispatch MCP/plugin surface. Not for changing dispatch source code; use AGENTS.md for implementation work.
metadata:
  short-description: Operate the dispatch control plane
---

# dispatch

Use `$dispatch` to operate the local dispatch control plane. dispatch owns one
`codex app-server` through a daemon and exposes one authored op registry through
CLI and MCP surfaces.

For source changes, read the repo-root `AGENTS.md` instead. This skill is for
using the tool.

## Command Surface

When you are in this repo, prefer the in-tree command:

```bash
uv run dispatch --help
```

The current canonical operator grammar is:

- health: `doctor`
- daemon process: `up`, `down`
- daemon reads: `daemon status`, `daemon log`
- registry recovery: `registry migrate`
- model catalog: `models`
- provider capacity: `usage`
- thread lifecycle/read/search: `new`, `attach`, `list`, `list --unmanaged`,
  `get`, `sync`, `tail`, `history`, `watch`, `search`, `query`
- thread actions: `rename`, `archive`, `restore`
- message verbs: `send`, `stop`
- goals: `goal status`, `goal set`, `goal clear`
- inbox/subscriptions: `subscribe`, `subscriptions`, `unsubscribe`, `inbox list`,
  `inbox read`, `inbox ack`
- triggers: `trigger add`, `trigger list`, `trigger rm`, `trigger pause`,
  `trigger resume`
- schemas/MCP: `schema <command>`, `mcp`

Successful CLI output is JSON-shaped. Use `--json` in scripts when you want the
machine-output contract to be explicit.

## Start Or Inspect The Daemon

```bash
uv run dispatch doctor --no-app-server
uv run dispatch up --json
uv run dispatch daemon status
uv run dispatch daemon log --limit 10
```

If a command fails because the running daemon does not support a current CLI op,
dispatch treats that as daemon/client skew. It restarts an idle daemon and retries
once. If the daemon has active work, it refuses to restart automatically; wait or
run `uv run dispatch down`, then `uv run dispatch up --json` when it is safe.

Use `uv run dispatch doctor` before relying on live thread operations in a new or
untrusted environment. It checks PATH visibility, Codex CLI/auth footprint,
daemon socket/pidfile state, registry schema/integrity, packaged skills/plugin
assets, and a low-risk Codex App Server initialize smoke. Use `--no-app-server`
when you only need local install/runtime diagnostics.

If doctor reports an old registry schema, run `uv run dispatch down`, then
`uv run dispatch registry migrate`, then `uv run dispatch up --json`.

Stop only when it is clearly your daemon/session to stop:

```bash
uv run dispatch down
```

Runtime state defaults to `~/.dispatch`. Use `DISPATCH_HOME` for isolation when
testing. Do not point tests at the user's live `~/.codex`; the repo integration
suite uses an isolated `CODEX_HOME`.

## Shell Completions

Use the derived completion command when setting up an operator shell:

```bash
uv run dispatch completion bash
uv run dispatch completion zsh
uv run dispatch completion fish
```

Evaluate the generated script for ad hoc use, or write it to the shell's
completion directory for durable installs.

## Thread Selectors And Lane Rules

Every managed thread has a stored dispatch-local `ref`. Prefer refs for command
arguments. The full Codex thread id is always accepted. Titles and `@handles`
are mutable convenience labels; use them only when a unique human label is more
useful than a ref.

Owned lanes are created by dispatch and are writable. Prefer `new` for a
configured managed thread; it applies `.dispatch/config.toml`, presets, name
prefixes, and can send an initial turn:

```bash
uv run dispatch new --name my-lane --cwd /path/to/project --text "Do the bounded thing."
uv run dispatch new --name my-lane --goal "Loop until green." --text "Start with tests."
uv run dispatch new --name my-lane --preset reviewer --no-send
```

Omit sandbox, approval, model, and service-tier settings when Codex defaults are
acceptable. `dispatch new` omits unset policy/model fields from `thread/start`
and initial `turn/start`, allowing Codex/App Server global, profile, and
project-local configuration to apply. Add explicit values only when the lane
needs Dispatch-owned overrides.

Use `--goal` for a native App Server goal before the initial turn. Do not put
`/goal ...` in `--text`; dispatch treats slash commands as plain text and rejects
that shape so agents do not create a thread that only looks goal-driven.

For durable or parallel launches, drive `new` from a **launch packet** directory
(`goal.md`, `prompt.md`, `output.schema.json`, `base.md`, `developer.md`,
`dispatch.toml`, plus staged-only `hooks/` and `codex/`) or from explicit files:

```bash
uv run dispatch new --name lane-a --cwd /repo --packet ./packet
uv run dispatch new --name lane-a --cwd /repo --goal-file goal.md --input-file prompt.md
printf 'goal text' | uv run dispatch new --name lane-a --goal-file - --input-file prompt.md
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --dry-run --json
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --stage all
```

Use `--input-file` for the prompt file (the file form of `--text`). Precedence
per slot is inline flag > explicit file > packet > repo config. Only one input may
read stdin (`-`).
`--dry-run` resolves and prints the plan (sources with byte/SHA-256, effective
settings, staged parts) without mutating any state. `--stage all|<parts>` writes
durable twins to `.agents/sessions/<ref>/` (with `--inline <parts>` to exclude
some); dispatch stages `hooks/`/`codex/` but never executes hooks. The current
App Server exposes no native worktree request; Dispatch's `--worktree create`
helper is a vanilla git preflight, not a Codex protocol feature.

For worktree-backed lanes, treat the launched runtime as the source of truth.
Dispatch should be given the exact `--cwd`; it should not assume fixed Codex
worktree paths such as `.codex/worktrees/<run>/<lane>` or
`~/.config/codex/worktrees/<name>`. Codex-managed worktrees may be detached or
unnamed, so an empty `git branch --show-current` is not automatically a failure.
Verify identity with `pwd`, `git rev-parse --show-toplevel`,
`git rev-parse --short HEAD`, `git status --short`, and any repo-provided runtime
or workspace doctor command. A branch name is useful metadata, not proof of
correctness unless the coordinator explicitly required a named branch.

If the repo provides `.codex/environments/environment.toml`, setup/teardown
hooks, or workspace bootstrap scripts, let repo-local tooling own those
semantics. Dispatch may stage the packet, hook files, and Codex config files so
the lane can inspect or run them, but Dispatch should not execute arbitrary hooks
or apply trust-sensitive config on the repo's behalf.

Use `--workspace` when Dispatch should resolve repo-local workspace metadata
before creating the thread:

```bash
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --workspace auto --dry-run --json
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --workspace auto --stage all
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --workspace none
```

`--workspace none` preserves the exact cwd path. `--workspace auto` discovers
`.codex/environments/environment.toml`, reports environment name/version,
setup/cleanup scripts, repo root, and effective cwd, and no-ops with
`state="not_found"` when no supported metadata exists. Dry runs never execute
setup. Setup scripts run only with explicit `--workspace-setup run` or local
daemon policy `[policy] allow_workspace_setup = true`; packet-local config is
not enough to grant setup execution.

Use `--worktree create` when Dispatch should create a vanilla git worktree before
launch:

```bash
uv run dispatch new --name lane-a --cwd /repo --worktree create --dry-run --json
uv run dispatch new --name lane-a --cwd /repo --worktree create --worktree-branch dispatch/lane-a
uv run dispatch new --name lane-a --cwd /repo --worktree create --worktree-path /tmp/lane-a
```

The default root is `~/.dispatch/worktrees/<repo>/<lane>/`, not a repo-local
`.dispatch/worktrees/` directory. `DISPATCH_WORKTREE_ROOT` can override the root.
Do not assume or mimic Claude/Codex private worktree path schemes; Dispatch
reports the exact path/branch/base/head it created. If a branch is already
checked out elsewhere, launch fails before thread creation and names the owning
worktree.

Workspace config can carry worktree defaults, with CLI flags winning:

```toml
[workspace]
default = "auto"
worktree = "create"
worktree_branch = "dispatch/default"
worktree_base = "HEAD"

[workspace.presets.athena]
mode = "auto"
worktree = "create"
worktree_branch = "dispatch/athena"
```

`new` returns `message_accepted`, not proof of assistant completion. After launch,
use `get` to check `latest_turn`, `tail` for persisted history, or `watch` for a
bounded live sample.

Before choosing explicit `--model`, `--model-provider`, or `--service-tier`
values, ask dispatch for the live catalog:

```bash
uv run dispatch models
uv run dispatch models --no-refresh
uv run dispatch schema models
```

Omit model/tier values when Codex defaults are acceptable. If a preset uses a
user-facing tier such as `fast`, Dispatch resolves it through `model/list`
service tiers before starting the thread. The catalog also reports model-defined
reasoning efforts, input modalities, personality support, and upgrade targets.
Do not guess current model ids or effort names from memory; use the catalog
output and its `aliases` field.

Use the redacted provider inventory before routing optional work by capacity:

```bash
uv run dispatch usage
uv run dispatch usage --no-refresh --json
uv run dispatch usage --provider codex --host local
uv run dispatch usage --all-hosts --no-refresh
uv run dispatch usage --include-daily --stale-after-seconds 300
uv run dispatch schema usage
```

Default `usage` refreshes supported local providers and omits daily buckets.
Use `--no-refresh` for a database-only read and `--include-daily` only when
historical detail is needed. The default host is `local`; use `--all-hosts` for
mesh inventory. Treat `stale: true`, `partial`, `signed_out`,
`disabled`, `unsupported`, and `unavailable` as explicit routing constraints.
Capacity, account, usage, and each window have independent freshness. Output is
masked/fingerprinted and never includes raw email, auth material, balances, or
reset-credit mutation ids.

Attached lanes are existing desktop Codex threads registered by raw thread id:

```bash
uv run dispatch attach <codex-thread-id>
uv run dispatch attach <codex-thread-id> --sync
```

Attached lanes are managed by dispatch but turn-writing/history-mutating
operations such as send, stop, goal mutation, fork, rollback, or compact are
blocked by default. ADR-0005 and
ADR-0018 keep this boundary locked because desktop Codex and dispatch run
separate app-server processes and there is no cross-process write interlock.
Local operators can opt in with `[policy] allow_attached_writes = true` in
`~/.dispatch/config.toml`; when that policy is enabled, Dispatch may send,
inject context, and set goals on attached lanes. Check `writable`,
`capabilities`, and `write_locked_reason` in `list --json` or `get --json`
before deciding whether to write:

```bash
uv run dispatch list --json | jq '.lanes[] | select(.writable)'
uv run dispatch list --json | jq '.lanes[] | select(.capabilities.context)'
```

Explicit metadata/lifecycle commands (`rename`, `archive`, `restore`) are
allowed regardless because they do not start turns or mutate turn history.

Attach is compact by default: it verifies the thread with
`thread/read(includeTurns:false)`, registers metadata, and does not resume turn
history. Use `--sync` or `sync` when you want dispatch to refresh its local
indexed view.

```bash
uv run dispatch sync <dispatch-ref-or-thread-id>
uv run dispatch sync <dispatch-ref-or-thread-id> --max-turns 20 --max-items 200
uv run dispatch sync <dispatch-ref-or-thread-id> --full --max-bytes 16777216
```

Sync establishes metadata-only live observation, indexes recent App Server history
first, and persists bounded turn/item cursors to reconcile missed newer turns before
later backwards continuation. It also indexes source identity, sync state, latest event time, latest turn id, and
bounded incremental JSONL facts when Codex exposes a rollout path. Check
`history_capability`, `history_complete`, `truncated`, page/item counts, scanned
bytes, and duration in JSON output. Older binaries fall back to metadata/JSONL
with an explicit unsupported capability. `turn-page-fallback` means turn paging
works but item paging does not; an atomic turn that exceeds the configured
persistence budget stays pending/truncated until sync is rerun with a larger
explicit budget. The aggregate byte target is checked between provider pages;
`scanned_bytes` may exceed it by one received page, but a page that would exceed
the remaining persistence budget is not indexed. One `--max-seconds` deadline
bounds metadata, provider history, local parsing, persistence, and archive
reconciliation. Durable cursor-cycle detection prevents repeated syncs from
spinning. Bare `history` reads the local index only.
When experimental paging is unavailable, sync retries stable metadata-only
resume and reports observation separately from history capability. An oversized
complete JSONL record remains at its current offset with an actionable
`--max-bytes` diagnostic rather than being silently skipped.
Selector-scoped transcript reads through `tail`, `history`, or
transcript-inclusive `get` still use App Server `thread/read(includeTurns:true)`
as the canonical source, and those reads backfill Dispatch's normalized local
history index with turns, items, and refs for that one thread. If the selector
is a raw unmanaged Codex thread id, `sync` first registers it as an attached
read/metadata-managed lane, then refreshes the index. That does not grant write
authority.

Sending to a raw unmanaged Codex thread id also performs that registration and
quick sync first. The write still follows the attached-lane policy: without
`allow_attached_writes`, Dispatch records the thread as managed/indexed and then
refuses the turn-writing action with an authority error. With the policy enabled,
the send path resumes the attached thread and starts the turn.

Sync also reconciles known App Server archive membership for the target. Archive
state is lifecycle metadata, not cleanup: dispatch does not delete provider
events or normalized history evidence during `archive`, `restore`, sync
reconciliation, or event indexing.

## History Capture Policy

Dispatch captures normalized history into its local SQLite registry. Default
`standard` capture keeps operational facts and bounded searchable history facts.
Live App Server item events and transcript replay share one canonical normalizer.
Codex 0.144 message, reasoning, command, file, MCP/dynamic/collaboration tool,
subagent, web, image, review, sleep, and compaction items become the same local
rows and refs in either path. Unknown future item types stay visible. Concrete
tool/server/status, arguments, errors, durations, files, and child-thread ids
stay queryable without raw retention. Raw provider payloads stay gated by
retention policy. Transcript replay is additive because provider history can
omit richer tool items already observed live, and lower-retention replay cannot
silently erase a payload retained earlier at a higher capture level. Normalized
text and command/tool metadata are bounded and redact common credential forms
and sensitive argument keys. Minimal capture keeps turn-level state but skips
item-level transcript rows. Bare `history` overview renders from the local
index. Selector-scoped `history` item/tool/file views render from the normalized
index after refreshing one thread; `--raw` keeps its live App Server raw-payload
behavior. Use debug capture only when developing or diagnosing
reducers/search/provider adapters; debug retention can store bounded raw
provider event and item payloads with truncation markers:

```toml
[history]
capture = "debug" # minimal | standard | debug
raw_payload_retention = "debug" # off | errors | debug | all
max_text_bytes = 8192
max_payload_bytes = 65536
```

Prefer isolated `DISPATCH_HOME` and `CODEX_HOME` for debug capture. Use
`dispatch doctor` to confirm the active capture mode; it warns when debug/raw
retention is enabled.

## Discover Sessions

`list` shows threads dispatch already manages. `list --unmanaged` lists
persisted Codex sessions that are not registered in dispatch. It uses App Server
`thread/list` in state-db-only mode, asking for active sessions sorted by recent
updates. It is read-only and does not resume or register anything:

```bash
uv run dispatch list
uv run dispatch list --unmanaged --limit 20
uv run dispatch list --unmanaged --archived --limit 20
uv run dispatch list --parent <ref-or-thread-id>
uv run dispatch list --ancestor <ref-or-thread-id>
uv run dispatch list --root <ref-or-thread-id>
uv run dispatch get <ref-or-thread-id> --topology
```

Topology does not imply authority. Parent/ancestor filters use App Server's
native spawned-thread relationships, while ordinary forks remain separate in
`forked_from` and `forks`. Unmanaged discovery excludes threads that already
have lanes. Plain reads use the local topology cache; `get --topology` performs
a bounded refresh. Check `complete`, `truncated`, and `cycle_detected` before
treating the result as a complete tree, and use `--topology-limit` to bound it.

Use a discovered session `id` with `attach <id>` or `sync <id>`. `list --unmanaged`
is read-only; `sync <id>` is the explicit step that registers the thread as an
attached lane.

## Search And Thread Actions

Use top-level actions when you want to work with either managed threads or raw
unmanaged Codex thread ids:

```bash
uv run dispatch rename @my-lane my-lane-final
uv run dispatch archive <codex-thread-id>
uv run dispatch restore @my-lane
```

`restore` only unarchives; it does not resume or start a turn.

Use `search` before attaching when you need App Server broad search over Codex
history, including unmanaged or not-yet-indexed threads:

```bash
uv run dispatch search "schema drift"
uv run dispatch search "schema drift" --managed
uv run dispatch search "schema drift" --unmanaged
uv run dispatch search "schema drift" --thread <dispatch-ref>
uv run dispatch search "schema drift" --repo .
uv run dispatch search "schema drift" --dir /path/to/project
uv run dispatch search "schema drift" --since 2026-06-01 --until 2026-06-05
```

Use `query` for Dispatch's local indexed managed-history substrate. Query does
not call App Server search and only sees threads Dispatch has indexed through
sync/history/tail/watch/live capture. Text is optional when a structural filter
is present:

```bash
uv run dispatch query "schema drift"
uv run dispatch query --tool linear.save_issue
uv run dispatch query --tool linear.save_issue --tool-status completed --arg-key id
uv run dispatch query --file convex/support/lineage.ts
uv run dispatch query --repo . --since 2026-06-01 --until 2026-06-05
uv run dispatch query --type mcpToolCall --errored
uv run dispatch query --mentions-thread 019f
uv run dispatch schema query
```

Query output includes normalized command/tool metadata and explicit child-thread
refs, so prefer those fields over parsing retained raw payloads with `jq`.

Use `history` after you already know the thread and want summary/items/tools/files
inspection. Sync is separate for managed lanes: it refreshes dispatch's local
index and does not grant write authority. For a raw unmanaged Codex id, `sync` is
also the explicit registration step.

## Message Verbs

`send` is the primary way to put work or context into a lane:

```bash
uv run dispatch send @my-lane "Do the bounded thing."
uv run dispatch send 019ead04-d2f4-77e2-acf7-f34d25456fa8 "Picking this up."
uv run dispatch send @my-lane "Focus on docs first." --steer
uv run dispatch send @my-lane "Stop and do this instead." --interject
uv run dispatch send @my-lane "Context: use lane publicly, thread internally." --context
uv run dispatch send @my-lane "After this finishes, summarize risks." --mode queue
uv run dispatch send @my-lane "Can you check this?" --intro
```

The mode flags and `--mode send|steer|queue|interject|context` are mutually
exclusive. `--queue` stores the message durably and starts one queued turn when
the lane is idle.

Use `--intro` when you are sending from one managed Codex thread to another and
want the recipient to know how to reply through dispatch. It derives the sender
from `CODEX_THREAD_ID`, so the current thread must already be managed. Intro
messages append the standard visible Dispatch attribution footer:

```text
<message>

dispatch (dm): [@Sender](codex://threads/<thread-id>) `<ref>`
↳ reply `dispatch send <ref> "..."`
```

Use `stop` to cancel the active turn without replacement text:

```bash
uv run dispatch stop <dispatch-ref>
```

## Inbox And Subscriptions

Use `subscribe` when the current managed Codex thread should hear about another
lane later. A subscription is an event-to-inbox binding. It creates a durable
inbox message when the event matches, then optionally starts a new turn in the
subscriber.

```bash
uv run dispatch subscribe @worker
uv run dispatch subscribe @worker when:done,delivery:inbox
uv run dispatch subscribe @worker --when approval --delivery inbox --repeat
uv run dispatch new --name worker --cwd /repo --text "Do it." --subscribe
uv run dispatch new --name worker --cwd /repo --text "Do it." --subscribe when:done,to:self
uv run dispatch new --name worker --cwd /repo --text "Do it." --subscribe-spec when:done,to:self
```

Default subscription settings are `when:done,to:self,delivery:turn,deliver:idle,
tail:1,once:true,ack:auto,attribution:true` when the subscriber is writable. If the
subscriber is an attached/read-only lane, the default falls back to
`delivery:inbox`; explicit `delivery:turn` still fails unless attached writes are
enabled. `self` is derived from `CODEX_THREAD_ID`, so the calling thread must
already be managed by dispatch. Use explicit `--to <ref>` when one managed lane
is subscribing on behalf of another.

Useful `when` buckets:

- `done`: completed or failed turns.
- `completed` / `failed`: one terminal outcome.
- `approval`: command, file-change, and permission approvals.
- `needs-attention`: approvals plus user-input, elicitation, and dynamic-tool requests.
- `idle`: idle status events.
- `activity`: any tracked lane event.

Turn-delivered subscription updates use the same visible Dispatch attribution
footer by default, with source thread link, ref, event, and when bucket.
Use `attribution:false` in the compact spec or `--no-attribution` when a subscriber
needs the older compact body without the footer.

Use inbox-only delivery when you want durable collection without waking the
subscriber:

```bash
uv run dispatch inbox list
uv run dispatch inbox list --lane <dispatch-ref> --state pending
uv run dispatch inbox read <message-id>
uv run dispatch inbox ack <message-id>
uv run dispatch inbox ack --all
uv run dispatch subscriptions
uv run dispatch unsubscribe <subscription-id> --yes --json
```

App Server interactive requests are durable and use one generic response path. Check the request's `expected_response` before answering; never put credentials in a response:

```bash
uv run dispatch request list --state pending --json
uv run dispatch request respond <request-id> '{"action":"decline"}' --json
uv run dispatch schema "request respond"
```

Owned requests default to `attention`; attached/unmanaged requests default to `deny`. Local policy can set `owned_interactive_requests` or `attached_interactive_requests` to `attention`, `deny`, or `permissive`, plus `interactive_request_timeout_seconds`. Permissive mode approves supported local approvals only; auth, attestation, and unknown host requests are never synthesized.

Destroy-intent commands prompt by default. In scripts, use explicit confirmation:

```bash
uv run dispatch archive <dispatch-ref> --yes --json
uv run dispatch trigger rm <trigger-id> --yes --json
```

For short inter-lane chat, use the companion `$dm` skill, which is backed by
`dispatch send`.

## History, Watch, And Goals

Use `get` for compact managed-thread metadata:

```bash
uv run dispatch get <dispatch-ref>
```

Check `latest_turn` when a message was accepted but no assistant work is visible.
It records the latest observed turn id, status, and App Server error text/time for
failed turns.

Use `tail` for persisted turn history:

```bash
uv run dispatch tail <dispatch-ref> --limit 50
```

`tail` uses App Server `includeTurns`, which is not available for ephemeral
threads. It also feeds the normalized local history index for the lane.

Use `history` for transcript inspection and rollups. Bare `history` summarizes
managed lanes; passing a selector drills into one thread and can show summary,
items, tools, or files:

```bash
uv run dispatch history
uv run dispatch history <dispatch-ref>
uv run dispatch history <dispatch-ref> --view tools
uv run dispatch history <dispatch-ref> --view files
uv run dispatch history <dispatch-ref> --view items --tool bash --grep "git status" --raw
uv run dispatch history <dispatch-ref> --view items --tool-server linear --tool-status completed --arg-key id
uv run dispatch history <dispatch-ref> --view items --mentions-thread 019f
uv run dispatch history --has-tool bash --changed --min-bytes 100000
```

Bare `history` includes transcript size, estimated tokens, active dates, deduped
tools, worktree identity, and dirty changed-file
names from each lane cwd. Overview filters include `--cwd`, `--source`,
`--status`, `--has-tool`, `--changed/--clean`, and `--min-bytes`. Item views use
`--type`, `--role`, `--phase`, `--tool`, `--tool-server`, `--tool-status`,
`--errored/--not-errored`, `--mentions-thread`, `--arg-key`, `--grep`, and
optional `--raw`. Bare overview reads the
local index only. Selector-scoped history reads refresh one thread and backfill
the normalized local history index. Normal item/tool/file views render from that
index after refresh; `--raw` intentionally reads the live App Server raw item
payloads for jq-heavy inspection.

Use `watch` for a bounded live event sample. It returns raw App
Server method/params until a limit or timeout, and it is not an infinite tail:

```bash
uv run dispatch watch <dispatch-ref> --limit 20 --timeout 10
```

Use native goals on owned lanes when a worker has a durable objective:

```bash
uv run dispatch goal set @my-lane "Loop until checks are green."
uv run dispatch goal status <dispatch-ref>
uv run dispatch goal clear <dispatch-ref>
```

Goals require non-ephemeral App Server threads.

`tail --follow` is not canonical; use `watch`.

## Markdown Thread Links

Use readable handles plus Codex thread URIs in human-facing text. Compose a
Markdown link whose label is the handle and whose destination is the Codex URI:

```markdown
label: @Target
destination: codex://threads/<codex-thread-id>
```

Use raw thread ids for `attach`. Use refs or full thread ids for dispatch
thread arguments.

## Triggers

A trigger binds `when -> action -> lane`.

```bash
uv run dispatch trigger add \
  --name pulse \
  --lane <dispatch-ref> \
  --when interval \
  --seconds 1800 \
  --action send \
  --text "Check in briefly."
```

Use `--idle-only`, `--min-interval`, and `--dedupe` to reduce noisy automation.
Remember that dedupe state is process-local and resets when the daemon restarts.

```bash
uv run dispatch trigger list
uv run dispatch trigger pause <trigger-id>
uv run dispatch trigger resume <trigger-id>
uv run dispatch trigger rm <trigger-id>
```

## Schemas

Use `schema` for derived input/output schemas:

```bash
uv run dispatch schema send
uv run dispatch schema "list --unmanaged"
uv run dispatch schema models
uv run dispatch schema usage
uv run dispatch schema "goal set"
```

Prefer `schema` for `jq`/automation field discovery. It is derived from the same
op registry as CLI and MCP, including composed spellings like `list --unmanaged`.

## MCP And Plugin

The MCP server is:

```bash
uv run dispatch mcp
```

The MCP surface is grouped for agent ergonomics, not one tool per CLI
subcommand. Tools are grouped by workflow and safety boundary, and each call
selects an `op` inside the tool. In this repo, the workspace-local Codex plugin
lives at `plugins/dispatch`. It exposes these skills and the same MCP registry.
Use the daemon-read MCP tool's `models` op before setting explicit model or
service-tier arguments, and its `usage` op before making capacity-based routing
decisions.
The thread-write MCP tool's `fork` op accepts `last_turn_id` to fork through one
completed turn, inclusive.
The thread-read MCP tool derives the same topology inputs and outputs used by
CLI `list` and `get`; topology reads never attach or grant write authority.
If the plugin does not appear immediately, restart Codex for the workspace.
Installed PyPI packages also include read-only copies of these skills and the
plugin bundle under `outfitter.dispatch.assets`; use the repo copies for editing.

## Guardrails

- Do not mutate source files, Git, PRs, Graphite, or tracker state as part of
  ordinary dispatch operation.
- Do not install launchd autostart unless the user explicitly asks.
- Start troubleshooting with `dispatch doctor`; use its recovery hints rather
  than guessing about stale sockets, PATH, auth, or registry shape.
- If doctor reports an old registry, stop the daemon and run
  `dispatch registry migrate` before starting it again.
- Do not describe `tail --follow` as canonical or streaming forever. Use `watch`
  for bounded live samples until dispatch grows a subscription-capable control socket.
- Do not treat `rollback` as file undo.
- If a request becomes long-running owned work, use a proper delegated lane or
  goal workflow rather than a casual message.
