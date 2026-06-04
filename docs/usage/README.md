# dispatch Usage

This is the operator path for dispatch v0. It covers how to start the daemon, create
lanes, send work, add triggers, and expose the same op registry through MCP.

For implementation guidance, use [`AGENTS.md`](../../AGENTS.md). For design context, use
[`docs/development/design.md`](../development/design.md) and [`docs/adrs/`](../adrs/).

## Install And Run Locally

In this repo, use `uv`:

```bash
uv sync
uv run dispatch --help
uv run dispatchd --help
```

Start the singleton daemon:

```bash
uv run dispatch up
uv run dispatch status
```

For foreground debugging, run the daemon directly:

```bash
uv run dispatchd run
```

Stop it when you are done:

```bash
uv run dispatch down
```

Runtime state defaults to `~/.dispatch`. Override it only when you need isolation:

```bash
DISPATCH_HOME=/tmp/dispatch-dev uv run dispatch up
```

The lower-level overrides are `DISPATCH_SOCKET`, `DISPATCH_DB`, and `DISPATCH_PIDFILE`.

## Lanes

An owned lane is a Codex thread created by dispatch. Owned lanes are writable. Use
`new` for the configured creation workflow:

```bash
uv run dispatch new \
  --name docs-review \
  --cwd /Users/mg/Developer/outfitter/dispatch \
  --text "Review the README for missing usage steps."
```

`new` reads `.dispatch/config.toml` when present, applies presets left-to-right,
decorates the name with the configured prefix, starts the lane, and sends the initial
message when `--text` is present. Use `--no-send` to create/configure the lane without
starting a turn:

```bash
uv run dispatch new --name docs-review --preset reviewer --no-send
```

`open` is the lower-level primitive:

```bash
uv run dispatch open --name docs-review --cwd /Users/mg/Developer/outfitter/dispatch
uv run dispatch roster
uv run dispatch send --lane @docs-review --text "Review the README for missing usage steps."
```

Example `.dispatch/config.toml`:

```toml
[defaults]
sandbox = "read-only"
approval_policy = "never"
prefix = "[${DISPATCH.CWD.REPO}]"

[defaults.instructions]
developer_file = ".dispatch/instructions/default.md"

[presets.reviewer]
effort = "high"
developer_file = ".dispatch/instructions/reviewer.md"

[presets.builder]
sandbox = "workspace-write"
approval_policy = "on-request"
developer_file = ".dispatch/instructions/builder.md"
```

Preset order matters: later presets win, and CLI flags win over presets.

Use `brief` for silent context injection. It adds model-visible context without starting a
turn:

```bash
uv run dispatch brief --lane @docs-review --text "Context: attached lanes are observe-only in v0."
```

Use `steer` only while the lane has an active turn:

```bash
uv run dispatch steer --lane @docs-review --text "Focus on operator docs first."
```

Use `interrupt` to cancel the active turn:

```bash
uv run dispatch interrupt --lane @docs-review
```

## Discover Sessions

`roster` lists the lanes dispatch already manages. `discover` is the other half: it lists the
persisted Codex sessions on this machine — desktop threads and prior runs — that you could
attach. It reads the Codex state DB directly (`thread/list`, state-db only), so it is fast and
read-only; it never resumes, writes, or registers anything.

```bash
uv run dispatch discover --limit 20
```

Each row carries `id`, `name`, a shortened `preview`, `cwd`, `status`, `source`, and
`ephemeral`. Use the `id` with `attach` to bring a session under management:

```bash
uv run dispatch attach --thread <id-from-discover>
```

Keep the two straight: `discover` shows attachable Codex sessions (not yet lanes); `roster`
shows managed lanes (owned or already attached).

## Attached Lanes

Attach registers an existing Codex thread by raw thread id:

```bash
uv run dispatch attach --thread <codex-thread-id>
```

Attached lanes are observe-only in v0. Dispatch can register and inspect them, but it must
not write to them because the desktop app uses a separate app-server process and there is no
cross-process write interlock. ADR-0005 is the authoritative decision:
[`docs/adrs/0005-lane-authority-capability-ladder.md`](../adrs/0005-lane-authority-capability-ladder.md).

Attach is bounded: the underlying `thread/resume` must complete within a short timeout
(15s). If the app-server is wedged and resume stalls, attach fails with a clear
`app_server` error and registers no lane — it never leaves a half-attached entry behind.
Re-run `attach` once the app-server is healthy.

When referring to a Codex thread in docs or prompts, prefer a readable handle with a URI:

```markdown
[@Dispatch](codex://threads/<codex-thread-id>)
```

Use the raw thread id for command arguments. Use the Markdown link in human-facing text.

## Triggers

A trigger binds `when -> action -> lane`.

Interval trigger:

```bash
uv run dispatch trigger-add \
  --name docs-pulse \
  --lane @docs-review \
  --when interval \
  --seconds 1800 \
  --action send \
  --text "Check whether the docs branch needs attention."
```

Cron trigger:

```bash
uv run dispatch trigger-add \
  --name weekday-standup \
  --lane @docs-review \
  --when cron \
  --cron "0 9 * * 1-5" \
  --action send \
  --text "Post a short standup summary."
```

Idle trigger:

```bash
uv run dispatch trigger-add \
  --name after-idle \
  --lane @docs-review \
  --when idle_for \
  --seconds 900 \
  --action brief \
  --text "If you resume, first re-read the current diff."
```

Useful guards:

- `--idle-only` fires only when the lane is idle.
- `--min-interval <seconds>` suppresses rapid refires.
- `--dedupe` suppresses identical consecutive firings within the current daemon process.

Manage triggers:

```bash
uv run dispatch trigger-list
uv run dispatch trigger-pause --id <trigger-id>
uv run dispatch trigger-resume --id <trigger-id>
uv run dispatch trigger-rm --id <trigger-id>
```

## MCP

The MCP surface is derived from the same op registry as the CLI. The local entrypoint is:

```bash
uv run dispatch mcp
```

MCP is grouped for agent ergonomics rather than one tool per op. Tools are grouped by
workflow and safety boundary, for example lane read/write/destroy, trigger
read/write/destroy, and daemon read tools. Each grouped call chooses an `op` inside the
tool, and that op's arguments/schema still derive from the same contract registry.

The workspace Codex plugin at [`plugins/dispatch/`](../../plugins/dispatch/) exposes that
MCP server through [`plugins/dispatch/.mcp.json`](../../plugins/dispatch/.mcp.json). The
workspace marketplace entry is [`.agents/plugins/marketplace.json`](../../.agents/plugins/marketplace.json).

If Codex does not pick up the plugin immediately, restart Codex for this workspace.

## Safety Notes

- Do not use dispatch tests or ad hoc integration probes against the user's live `~/.codex`.
  The integration suite uses an isolated `CODEX_HOME`.
- Do not expect attached lanes to receive live event fan-out across processes. The Phase-1
  spike confirmed cross-process history discovery/resume, not live co-presence.
- Do not install the generated launchd plist with `launchctl` unless the user explicitly
  wants persistent autostart.
- `show` currently reports lane metadata. It is not a transcript viewer yet.
