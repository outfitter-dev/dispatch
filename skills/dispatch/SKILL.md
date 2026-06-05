---
name: dispatch
description: Use when operating dispatch, creating or attaching Codex lanes, sending/steering/context-injecting/stopping through dispatch, managing triggers, checking daemon status/logs, or configuring the dispatch MCP/plugin surface. Not for changing dispatch source code; use AGENTS.md for implementation work.
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
- thread lifecycle/read/search: `new`, `attach`, `list`, `list --unmanaged`,
  `get`, `sync`, `tail`, `watch`, `search`
- thread actions: `rename`, `archive`, `restore`
- message verbs: `send`, `stop`
- goals: `goal status`, `goal set`, `goal clear`
- triggers: `trigger add`, `trigger list`, `trigger rm`, `trigger pause`,
  `trigger resume`
- schemas/MCP: `schema <command>`, `mcp`

Successful CLI output is JSON-shaped. Use `--json` in scripts when you want the
machine-output contract to be explicit.

## Start Or Inspect The Daemon

```bash
uv run dispatch doctor --no-app-server
uv run dispatch up
uv run dispatch daemon status
uv run dispatch daemon log --limit 10
```

Use `uv run dispatch doctor` before relying on live thread operations in a new or
untrusted environment. It checks PATH visibility, Codex CLI/auth footprint,
daemon socket/pidfile state, registry schema/integrity, packaged skills/plugin
assets, and a low-risk Codex App Server initialize smoke. Use `--no-app-server`
when you only need local install/runtime diagnostics.

Stop only when it is clearly your daemon/session to stop:

```bash
uv run dispatch down
```

Runtime state defaults to `~/.dispatch`. Use `DISPATCH_HOME` for isolation when
testing. Do not point tests at the user's live `~/.codex`; the repo integration
suite uses an isolated `CODEX_HOME`.

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
uv run dispatch new --name my-lane --preset reviewer --no-send
```

Attached lanes are existing desktop Codex threads registered by raw thread id:

```bash
uv run dispatch attach <codex-thread-id>
uv run dispatch attach <codex-thread-id> --sync
```

Attached lanes are managed by dispatch but are not turn-writable in v0. Do not
try turn-writing or history-mutating commands such as `send`, `stop`,
`goal set`, `goal clear`, `lane fork`, `lane rollback`, or `lane compact` on
attached lanes. Explicit metadata/lifecycle commands (`rename`, `archive`,
`restore`) are allowed because they do not start turns or mutate turn history.
ADR-0005 and ADR-0018 keep this boundary locked because desktop Codex and
dispatch run separate app-server processes and there is no cross-process write
interlock.

Attach is compact by default: it verifies the thread with
`thread/read(includeTurns:false)`, registers metadata, and does not resume turn
history. Use `--sync` or `sync` when you want dispatch to refresh its local
indexed view.

```bash
uv run dispatch sync <dispatch-ref-or-thread-id>
uv run dispatch sync <dispatch-ref-or-thread-id> --full
```

Sync indexes source identity, sync state, latest event time, latest turn id, and
bounded top+tail JSONL facts when Codex exposes a rollout path. It does not copy
the full transcript by default and it does not grant write authority to attached
lanes.

## Discover Sessions

`list` shows threads dispatch already manages. `list --unmanaged` lists
persisted Codex sessions that are not registered in dispatch. It uses App Server
`thread/list` in state-db-only mode. It is read-only and does not resume or
register anything:

```bash
uv run dispatch list
uv run dispatch list --unmanaged --limit 20
```

Use a discovered session `id` with `attach <id>`.

## Search And Thread Actions

Use top-level actions when you want to work with either managed threads or raw
unmanaged Codex thread ids:

```bash
uv run dispatch rename @my-lane my-lane-final
uv run dispatch archive <codex-thread-id>
uv run dispatch restore @my-lane
```

`restore` only unarchives; it does not resume or start a turn.

Use `search` before attaching when you need to find the right existing thread:

```bash
uv run dispatch search "schema drift"
uv run dispatch search "schema drift" --managed
uv run dispatch search "schema drift" --unmanaged
uv run dispatch search "schema drift" --thread <dispatch-ref>
uv run dispatch search "schema drift" --repo .
uv run dispatch search "schema drift" --dir /path/to/project
uv run dispatch search "schema drift" --since 2026-06-01 --until 2026-06-05
```

Broad search uses experimental App Server `thread/search` plus dispatch-side
filters. Lane-focused search reads one thread transcript and scans locally.
Sync is separate: `sync` refreshes dispatch's local index for a managed
lane, but it does not attach unmanaged sessions or grant write authority.

## Message Verbs

`send` is the primary way to put work or context into a lane:

```bash
uv run dispatch send @my-lane "Do the bounded thing."
uv run dispatch send @my-lane "Focus on docs first." --steer
uv run dispatch send @my-lane "Stop and do this instead." --interject
uv run dispatch send @my-lane "Context: use lane publicly, thread internally." --context
uv run dispatch send @my-lane "After this finishes, summarize risks." --mode queue
```

The mode flags and `--mode send|steer|queue|interject|context` are mutually
exclusive. `--queue` stores the message durably and starts one queued turn when
the lane is idle.

Use `stop` to cancel the active turn without replacement text:

```bash
uv run dispatch stop <dispatch-ref>
```

For short inter-lane chat, use the companion `$dm` skill, which is backed by
`dispatch send`.

## History, Watch, And Goals

Use `get` for compact managed-thread metadata:

```bash
uv run dispatch get <dispatch-ref>
```

Use `tail` for persisted turn history:

```bash
uv run dispatch tail <dispatch-ref> --limit 50
```

`tail` uses App Server `includeTurns`, which is not available for ephemeral
threads.

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
uv run dispatch schema "goal set"
```

## MCP And Plugin

The MCP server is:

```bash
uv run dispatch mcp
```

The MCP surface is grouped for agent ergonomics, not one tool per CLI
subcommand. Tools are grouped by workflow and safety boundary, and each call
selects an `op` inside the tool. In this repo, the workspace-local Codex plugin
lives at `plugins/dispatch`. It exposes these skills and the same MCP registry.
If the plugin does not appear immediately, restart Codex for the workspace.
Installed PyPI packages also include read-only copies of these skills and the
plugin bundle under `outfitter.dispatch.assets`; use the repo copies for editing.

## Guardrails

- Do not mutate source files, Git, PRs, Graphite, or tracker state as part of
  ordinary dispatch operation.
- Do not install launchd autostart unless the user explicitly asks.
- Start troubleshooting with `dispatch doctor`; use its recovery hints rather
  than guessing about stale sockets, PATH, auth, or registry shape.
- Do not describe `tail --follow` as canonical or streaming forever. Use `watch`
  for bounded live samples until dispatch grows a subscription-capable control socket.
- Do not treat `rollback` as file undo.
- If a request becomes long-running owned work, use a proper delegated lane or
  goal workflow rather than a casual message.
