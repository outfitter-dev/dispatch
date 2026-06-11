# dispatch Usage

This is the operator path for dispatch. It covers how to start the daemon, create
lanes, send work, add triggers, and expose the same op registry through MCP.

For implementation guidance, use [`AGENTS.md`](../../AGENTS.md). For design context, use
[`docs/development/design.md`](../development/design.md) and [`docs/adrs/`](../adrs/).

## Install And Run Locally

Install the CLI from PyPI:

```bash
uv tool install outfitter-dispatch
dispatch --help
dispatchd --help
dispatch doctor
```

Upgrade or remove the installed tool with:

```bash
uv tool upgrade outfitter-dispatch
uv tool uninstall outfitter-dispatch
```

The PyPI package installs the `dispatch` and `dispatchd` commands, including
the `dispatch mcp` entrypoint. It also ships read-only copies of the first-party
`dispatch` and `dm` skills plus the local plugin bundle under the installed
`outfitter.dispatch.assets` package. Edit source assets in this repository under
`skills/` and `plugins/dispatch/`; installed copies are for setup and inspection.

Use this clean-machine smoke after installing or upgrading:

```bash
dispatch doctor
dispatch schema send
dispatch models --no-refresh
dispatch up --json
dispatch daemon status
dispatch down --json
```

If `dispatch doctor` fails before the app-server smoke because the Codex CLI is
not installed or authenticated, fix that first and rerun the doctor. Use
`dispatch doctor --no-app-server` when you only need to inspect package, PATH,
daemon, and registry state without starting a Codex App Server process.

For development from this repo, use `uv`:

```bash
uv sync
uv run dispatch --help
uv run dispatchd --help
uv run dispatch doctor --no-app-server
```

Start the singleton daemon:

```bash
uv run dispatch up
uv run dispatch daemon status
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

## Doctor And Recovery

`dispatch doctor` is the first diagnostic command for users and agents. It returns JSON
by default and exits non-zero only when a check fails:

```bash
dispatch doctor
dispatch doctor --text
dispatch doctor --no-app-server
```

Checks include:

- PATH visibility for `dispatch` and `dispatchd`.
- `codex --version` and Codex auth-file presence without reading secret contents.
- daemon reachability, socket path, pidfile, and stale runtime files.
- registry database readability, SQLite `quick_check`, required tables, and schema version.
- packaged `dispatch`/`dm` skills and plugin MCP config.
- low-risk `codex app-server --listen stdio://` initialize smoke.

Common recovery paths:

- Missing `dispatch` or `dispatchd`: install with `uv tool install outfitter-dispatch`;
  if uv reports the tool is installed but the shell cannot see it, run
  `uv tool update-shell` and restart the shell/Codex context.
- Missing `codex`: install or expose Codex CLI, then verify `codex --version` in the
  same environment that will run dispatch.
- Missing Codex auth: run `codex login` or start Codex once. The doctor only checks
  for auth material; it does not print or parse credentials.
- Stale socket or pidfile: run `dispatch down`, then `dispatch up`. If you are using
  isolated state, confirm `DISPATCH_HOME`, `DISPATCH_SOCKET`, and `DISPATCH_PIDFILE`.
- Registry schema newer than the installed binary: upgrade with
  `uv tool upgrade outfitter-dispatch` before starting the daemon.
- Registry schema older than the installed binary: run `dispatch down`, then
  `dispatch registry migrate`, then `dispatch up`. Migration backs up the registry
  by default and refuses to run while the daemon is reachable unless
  `--allow-running` is explicitly set for a controlled recovery.
- Registry integrity failure: stop the daemon, back up the database at the path shown
  by doctor, and recreate it or inspect with `sqlite3`.
- App Server initialize failure: run `codex app-server --listen stdio://` directly in
  the same shell and fix the Codex CLI/auth problem before relying on thread operations.

## Release Publishing

Maintainers publish `outfitter-dispatch` through PyPI Trusted Publishing from
GitHub Actions. The PyPI pending/trusted publisher must match:

- project: `outfitter-dispatch`
- repository: `outfitter-dev/dispatch`
- workflow: `publish.yml`
- environment: `pypi`

Publishing is triggered by a published GitHub Release. Before creating a
release, bump `project.version` in `pyproject.toml`, run:

```bash
just check
```

Then create and publish a GitHub Release for the same tag, for example
`v0.1.0`. Do not upload with a long-lived PyPI token unless the trusted
publisher path is unavailable and the maintainer explicitly chooses that
fallback.

## Lanes

An owned lane is a Codex thread created by dispatch. Owned lanes are writable. Use
`new` for the configured creation workflow:

```bash
uv run dispatch new \
  --name docs-review \
  --cwd /path/to/dispatch \
  --goal "Review until no P2 findings remain." \
  --text "Review the README for missing usage steps."
```

`new` reads `.dispatch/config.toml` when present, applies presets left-to-right,
decorates the name with the configured prefix, starts the lane, and sends the initial
message when `--text` is present. Use `--no-send` to create/configure the lane without
starting a turn:

```bash
uv run dispatch new --name docs-review --preset reviewer --no-send
```

Use `new --no-send` when you want to create the lane first and send later:

```bash
uv run dispatch new --name docs-review --cwd /path/to/dispatch --no-send
uv run dispatch list
uv run dispatch send <dispatch-ref> "Review the README for missing usage steps."
```

Every managed thread gets a dispatch-local `ref`, for example `0k7M4a`. Use refs
for day-to-day commands. The full Codex thread id is still the canonical global
identity and is accepted everywhere. Titles and `@handles` are mutable labels;
they are convenient, but not stable identity.

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

[presets.fast]
service_tier = "fast"
effort = "low"
```

Preset order matters: later presets win, and CLI flags win over presets.
Omit `model` unless you intentionally want Codex to use an explicit model. An
omitted model or service tier keeps the Codex default call shape; Dispatch still
records the configured default reported by `config/read` when it is available.

Use `models` before pinning model or service-tier presets:

```bash
uv run dispatch models
uv run dispatch models --no-refresh
uv run dispatch schema models
```

`models` refreshes from App Server `model/list` by default and reports the
configured default from `config/read`, each model's reasoning efforts, service
tiers, and aliases. For example, the user-facing `fast` alias resolves through
the advertised service tier named `Fast` and may send `serviceTier:"priority"`
to the App Server. If a requested tier is unavailable for the selected/default
model, `new` fails before starting the thread and prints the available tiers.
`--no-refresh` reads the local catalog cache plus current config defaults.

Use `--goal` to create a native App Server goal before the initial message is sent.
Slash commands in `--text` are not interpreted by dispatch; `--text "/goal ..."`
is rejected so agents do not accidentally create a thread that looks goal-driven but
has no native goal state. Goals require non-ephemeral threads, so `new --goal`
cannot be combined with `--ephemeral`.

The `new` response reports `message_accepted` and `goal_set`. `message_accepted`
means the App Server accepted the initial turn request; it does not prove the
assistant produced work. Use `get` to inspect `latest_turn`, `tail` for persisted
history, or `watch` for a bounded live event sample after launch.

Use `send --context` for silent context injection. It adds model-visible context without
starting a turn:

```bash
uv run dispatch send @docs-review "Context: attached lanes are not turn-writable in v0." --context
```

Use `send --steer` only while the lane has an active turn:

```bash
uv run dispatch send @docs-review "Focus on operator docs first." --steer
```

Use `send --interject` to cancel the active turn and start a replacement message:

```bash
uv run dispatch send @docs-review "Stop that and focus on operator docs first." --interject
```

Use `stop` to cancel the active turn without sending replacement text:

```bash
uv run dispatch stop <dispatch-ref>
```

Use `send --queue` when delivery should wait for the lane to become idle. The message is
stored in dispatch's durable registry and starts one queued turn per idle transition:

```bash
uv run dispatch send @docs-review "Run this after the active turn." --queue
```

Use `send --intro` for managed Codex-to-Codex coordination. It prepends a terse
reply hint like `[dispatch] From @Dispatch (<ref>). Use dispatch send ... to
reply.` The sender is derived from `CODEX_THREAD_ID`, so the current Codex
thread must already be managed by dispatch:

```bash
uv run dispatch send @docs-review "Can you sanity-check this?" --intro
```

## Thread History, Watch, And Goals

`get` is the compact managed-thread summary:

```bash
uv run dispatch get <dispatch-ref>
```

The response includes `latest_turn` when dispatch has observed turn lifecycle events:
the latest turn id, runtime status (`started`, `completed`, or `failed`), and the
last App Server error message/time when a turn fails. This is the first place to look
when a send or initial message was accepted but no assistant output appears.

Use `tail` when you want persisted turn history. It reads `thread/read` with
`includeTurns:true` and returns a compact item list; it is a history snapshot, not a
full execution log. App Server does not support `includeTurns` on ephemeral threads.

```bash
uv run dispatch tail <dispatch-ref> --limit 50
```

Use `watch` for a bounded live event sample from dispatch's app-server stream.
It returns raw App Server method names and params for the selected lane until `--limit`
events arrive or `--timeout` elapses. It is intentionally bounded because the current
control socket is request/response JSONL, not a subscription protocol.

```bash
uv run dispatch watch <dispatch-ref> --limit 20 --timeout 10
```

Native App Server goals can be read, set, and cleared on owned lanes:

```bash
uv run dispatch goal status @docs-review
uv run dispatch goal set @docs-review "Review until no P2 findings remain."
uv run dispatch goal clear @docs-review
```

Creating a goal requires an objective. After a goal exists, `goal set` can update
`--status` or `--token-budget`. App Server goals require non-ephemeral threads.

`tail --follow` is not canonical; use `watch`. True long-lived streaming will use a
future subscription-capable watch surface.

## Thread Actions And Search

`rename`, `archive`, and `restore` are top-level thread actions. They accept a managed
dispatch ref, a full Codex thread id, or a unique convenience label:

```bash
uv run dispatch rename <dispatch-ref> docs-review-final
uv run dispatch archive <dispatch-ref>
uv run dispatch restore <codex-thread-id>
```

`restore` unarchives the thread only; it does not resume the thread or start a new turn.
Destroy-intent commands prompt on a TTY. In scripts, use `--yes --json`; if you also
set `--no-interactive`, `--yes` is required or the command exits with a usage error.

Use `search` to search Codex thread history without first attaching every thread:

```bash
uv run dispatch search "schema drift"
uv run dispatch search "schema drift" --managed
uv run dispatch search "schema drift" --unmanaged
uv run dispatch search "schema drift" --thread <dispatch-ref>
uv run dispatch search "schema drift" --repo .
uv run dispatch search "schema drift" --dir /path/to/project
uv run dispatch search "schema drift" --since 2026-06-01 --until 2026-06-05
```

Broad search uses the App Server experimental `thread/search` primitive, then applies
dispatch-side filters for managed/unmanaged state, repo/directory, and date bounds. Lane
focused search reads that one thread with `thread/read(includeTurns:true)` and performs a
local substring scan. Date bounds accept ISO dates or datetimes and default to filtering
`updated_at`; use `--date-field created_at` when creation time matters.

## Discover Sessions

`list` shows the threads dispatch already manages. `list --unmanaged` is the other
half: it lists the persisted Codex sessions on this machine — desktop threads and prior
runs — that you could attach. It uses App Server `thread/list` in state-db-only mode,
asks for active sessions sorted by recent updates, and remains read-only; it never
resumes, writes, or registers anything.

```bash
uv run dispatch list --unmanaged --limit 20
```

Each row carries `id`, `name`, a shortened `preview`, `cwd`, `status`, `source`, and
`ephemeral`. Use the `id` with `attach` to bring a session under management:

```bash
uv run dispatch attach <id-from-list-unmanaged>
uv run dispatch attach <id-from-list-unmanaged> --sync
```

Keep the two straight: `list --unmanaged` shows unmanaged Codex sessions that are not
registered in dispatch; `list` shows managed threads (owned or already attached). Sync is
separate from both: `sync` refreshes dispatch's local index for a managed thread, but it
does not change ownership or write authority.

## Attached Lanes

Attach registers an existing Codex thread by raw thread id:

```bash
uv run dispatch attach <codex-thread-id>
uv run dispatch sync <dispatch-ref-or-thread-id>
```

Attached lanes allow observation, sync, and explicit metadata/lifecycle actions such as
`rename`, `archive`, and `restore`. Dispatch still must not write turns or mutate history on
attached lanes because the desktop app uses a separate app-server process and there is no
cross-process write interlock. ADR-0005 and ADR-0018 are the authoritative decisions:
[`docs/adrs/0005-lane-authority-capability-ladder.md`](../adrs/0005-lane-authority-capability-ladder.md)
and [`docs/adrs/0018-top-level-thread-actions-and-search.md`](../adrs/0018-top-level-thread-actions-and-search.md).

Attach is compact by default: it verifies the thread with App Server
`thread/read(includeTurns:false)`, registers the lane, and stores metadata sync state. It
does not call `thread/resume` or load turn history. If the app-server is wedged and the
metadata read stalls, attach fails with a clear `app_server` error and registers no lane —
it never leaves a half-attached entry behind.

Use `sync` to refresh dispatch's local indexed view of an attached lane. Sync reads the
official metadata and, when Codex exposes a local rollout path, parses bounded top+tail JSONL
records into a compact cache: source file identity, sync state, latest event timestamp,
latest turn id, and a preview. It does not copy the full transcript by default.

```bash
uv run dispatch sync <dispatch-ref-or-thread-id>
uv run dispatch sync <dispatch-ref-or-thread-id> --full
uv run dispatch get <dispatch-ref-or-thread-id>
uv run dispatch list
```

`sync --full` scans the whole current source file and marks the cache complete for that
file identity. It is still an index refresh, not a write to the Codex thread. `tail`
continues to use official `thread/read(includeTurns:true)` persisted history when you want
App Server turn summaries.

When referring to a Codex thread in docs or prompts, prefer a readable handle with a URI:

```markdown
[@Dispatch](codex://threads/<codex-thread-id>)
```

Use refs or raw thread ids for command arguments. Use the Markdown link in human-facing text.

## Triggers

A trigger binds `when -> action -> lane`.

Interval trigger:

```bash
uv run dispatch trigger add \
  --name docs-pulse \
  --lane <dispatch-ref> \
  --when interval \
  --seconds 1800 \
  --action send \
  --text "Check whether the docs branch needs attention."
```

Cron trigger:

```bash
uv run dispatch trigger add \
  --name weekday-standup \
  --lane <dispatch-ref> \
  --when cron \
  --cron "0 9 * * 1-5" \
  --action send \
  --text "Post a short standup summary."
```

Idle trigger:

```bash
uv run dispatch trigger add \
  --name after-idle \
  --lane <dispatch-ref> \
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
uv run dispatch trigger list
uv run dispatch trigger pause <trigger-id>
uv run dispatch trigger resume <trigger-id>
uv run dispatch trigger rm <trigger-id> --yes --json
```

## Schemas

Successful CLI output is JSON-shaped for `jq` by default. Use `--json` when you want to
make that contract explicit in scripts. `schema` prints the input and output schemas
derived from the contract registry:

```bash
uv run dispatch list --json
uv run dispatch schema send
uv run dispatch schema "list --unmanaged"
uv run dispatch schema sync
uv run dispatch schema watch
uv run dispatch schema models
uv run dispatch schema "goal set"
```

`schema` uses the CLI projection manifest, including composed command spellings such
as `list --unmanaged`. It is the preferred way to discover stable fields for `jq`
instead of scraping `--help` or hand-copying Pydantic schemas.

## MCP

The MCP surface is derived from the same op registry as the CLI. The local entrypoint is:

```bash
uv run dispatch mcp
```

MCP is grouped for agent ergonomics rather than one tool per op. Tools are grouped by
workflow and safety boundary, for example thread read/write/destroy, trigger
read/write/destroy, and daemon read tools. The daemon read tool includes the
`models` op so agents can discover valid model/service-tier choices without
guessing. Each grouped call chooses an `op` inside the tool, and that op's
arguments/schema still derive from the same contract registry.
Structured MCP outputs that identify a managed thread include the dispatch `ref`, full
Codex id, title/handle, managed/source/status, and cwd when available.

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
- `tail` is a persisted history snapshot. `watch` is a bounded live event sample.
  Neither is a durable infinite tail yet; that needs a subscription-capable control socket.
- `rollback` does not revert workspace files. Use Git or another workspace mechanism for
  file-level undo.
