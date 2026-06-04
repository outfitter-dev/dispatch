---
name: dispatch
description: Use when operating dispatch, opening or attaching Codex lanes, sending/steering/briefing/interruption through dispatch, managing triggers, checking daemon status/logs, or configuring the dispatch MCP/plugin surface. Not for changing dispatch source code; use AGENTS.md for implementation work.
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

If dispatch is installed into the environment, `dispatch --help` is fine.

The v0 ops are:

- lifecycle: `up`, `down`, `status`, `log`
- lanes: `new`, `open`, `attach`, `show`, `roster`, `archive`
- discovery: `discover`
- messages: `send`, `steer`, `brief`, `interrupt`
- triggers: `trigger-add`, `trigger-list`, `trigger-rm`, `trigger-pause`,
  `trigger-resume`
- MCP: `mcp`

## Start Or Inspect The Daemon

```bash
uv run dispatch up
uv run dispatch status
uv run dispatch log --limit 10
```

Stop only when it is clearly your daemon/session to stop:

```bash
uv run dispatch down
```

Runtime state defaults to `~/.dispatch`. Use `DISPATCH_HOME` for isolation when
testing. Do not point tests at the user's live `~/.codex`; the repo integration
suite uses an isolated `CODEX_HOME`.

## Lane Rules

Owned lanes are created by dispatch and are writable. Prefer `new` for a
configured lane; it applies `.dispatch/config.toml`, presets, name prefixes, and
can send an initial turn:

```bash
uv run dispatch new --name my-lane --cwd /path/to/project --text "Do the bounded thing."
uv run dispatch new --name my-lane --preset reviewer --no-send
```

Use `open` only when you need the lower-level primitive:

```bash
uv run dispatch open --name my-lane --cwd /path/to/project
uv run dispatch send --lane @my-lane --text "Do the bounded thing."
```

Attached lanes are existing desktop Codex threads registered by raw thread id:

```bash
uv run dispatch attach --thread <codex-thread-id>
```

Attached lanes are observe-only in v0. Do not try to `send`, `steer`, `brief`,
`interrupt`, or `archive` attached lanes. ADR-0005 keeps those writes locked
because desktop Codex and dispatch run separate app-server processes and there
is no cross-process write interlock.

`attach` is bounded: if the app-server stalls, the underlying `thread/resume`
times out (~15s) and `attach` fails with a clear `app_server` error, registering
no lane. There is no half-attached state to clean up — just retry once the
app-server is healthy.

## Discover Sessions

`discover` lists persisted Codex sessions you could attach (desktop threads and
prior runs), read straight from the Codex state DB. It is read-only and does not
resume or register anything:

```bash
uv run dispatch discover --limit 20
```

Use it to find a session `id`, then `attach --thread <id>`. It is distinct from
`roster`: `discover` shows attachable sessions (not yet lanes), `roster` shows
lanes dispatch already manages.

## Message Verbs

- `send` starts a turn. Use it for normal visible requests.
- `brief` injects model-visible context without starting a turn.
- `steer` interjects into an active turn and requires that the daemon knows the
  lane's active turn id.
- `interrupt` cancels the active turn.

For short inter-lane chat, use the companion `$dm` skill, which is a "dispatch
message" workflow backed by `dispatch send`.

## Markdown Thread Links

Use readable handles plus Codex thread URIs in human-facing text. Compose a
Markdown link whose label is the handle and whose destination is the Codex URI:

```markdown
label: @Dispatch
destination: codex://threads/<codex-thread-id>
```

Use raw thread ids for `attach --thread`. Use lane ids or `@handles` for
dispatch lane arguments.

## Triggers

A trigger binds `when -> action -> lane`.

```bash
uv run dispatch trigger-add \
  --name pulse \
  --lane @my-lane \
  --when interval \
  --seconds 1800 \
  --action send \
  --text "Check in briefly."
```

Use `--idle-only`, `--min-interval`, and `--dedupe` to reduce noisy automation.
Remember that dedupe state is process-local and resets when the daemon restarts.

## MCP And Plugin

The MCP server is:

```bash
uv run dispatch mcp
```

The MCP surface is grouped for agent ergonomics, not one tool per op. Tools are
grouped by workflow and safety boundary, and each call selects an `op` inside the
tool. In this repo, the workspace-local Codex plugin lives at `plugins/dispatch`.
It exposes these skills and the same MCP registry. If the plugin does not appear
immediately, restart Codex for the workspace.

## Guardrails

- Do not mutate source files, Git, PRs, Graphite, or tracker state as part of
  ordinary dispatch operation.
- Do not install launchd autostart unless the user explicitly asks.
- Do not promise transcript harvesting through `dispatch show`; v0 `show`
  reports lane metadata, not a full thread transcript.
- If a request becomes long-running owned work, use a proper delegated lane or
  goal workflow rather than a casual message.
