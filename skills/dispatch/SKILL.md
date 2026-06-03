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
- lanes: `open`, `attach`, `show`, `roster`, `archive`
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

Owned lanes are created by dispatch and are writable:

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

In this repo, the workspace-local Codex plugin lives at `plugins/dispatch`. It
exposes these skills and the same MCP registry. If the plugin does not appear
immediately, restart Codex for the workspace.

## Guardrails

- Do not mutate source files, Git, PRs, Graphite, or tracker state as part of
  ordinary dispatch operation.
- Do not install launchd autostart unless the user explicitly asks.
- Do not promise transcript harvesting through `dispatch show`; v0 `show`
  reports lane metadata, not a full thread transcript.
- If a request becomes long-running owned work, use a proper delegated lane or
  goal workflow rather than a casual message.
