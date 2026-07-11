# dispatch

Local control plane for orchestrating Codex agent lanes over the Codex App Server.
One authored contract per operation, projected to CLI + MCP (+ remote later) with no drift.

## Quick Start

Install the CLI from PyPI:

```bash
uv tool install outfitter-dispatch
dispatch --help
dispatchd --help
dispatch doctor
dispatch up --json
dispatch down --json
```

From a source checkout:

```bash
uv sync
uv run dispatch --help
uv run dispatch doctor --no-app-server
uv run dispatch models --no-refresh
uv run dispatch permissions --no-refresh
uv run dispatch usage --no-refresh
uv run dispatch up --json
uv run dispatch daemon status
```

Create an owned managed thread, send it work, and inspect the daemon:

```bash
uv run dispatch new \
  --name docs \
  --cwd /path/to/dispatch \
  --goal "Finish the docs review." \
  --text "Please summarize the current stack state." \
  --image ./stack.png
uv run dispatch list
uv run dispatch get <dispatch-ref>
uv run dispatch tail <dispatch-ref> --limit 20
uv run dispatch daemon log --limit 10
uv run dispatch down --json
```

For durable or parallel launches, point `new` at a launch packet directory and
preview it without side effects: `dispatch new --name lane-a --cwd /repo --packet
./packet --dry-run --json`, then `--stage all` to write durable session files under
`.agents/sessions/<ref>/`. See [`docs/usage/README.md`](docs/usage/README.md) for
packet layout, file/stdin inputs, and staging.

`new` and `send` accept repeatable `--image PATH` and `--image-url HTTPS_URL` options, with an optional `--image-detail auto|low|high|original`. Local images may be PNG, JPEG, GIF, or WebP and must be at most 20 MiB; remote URLs must resolve publicly and are fetched into ephemeral inline inputs without storing bytes. `send --input-file -` reads message text from stdin. Images work with normal sends, steering, durable queues, and interjection; silent `--context` injection remains text-only.

Use owned managed threads for turn-writing work. Existing desktop Codex threads can be attached as
managed threads, but ADR-0005 blocks turn-writing and history-mutating commands such
as `send`, `stop`, `goal set`, and `goal clear` on attached lanes by default. A
local operator can explicitly opt in with `[policy] allow_attached_writes = true`
in `~/.dispatch/config.toml`; `list --json` and `get --json` expose
`writable`, `capabilities`, and `write_locked_reason` so scripts can tell which
lanes can receive writes. Every managed thread has a dispatch-local `ref`; full
Codex thread UUIDs remain accepted everywhere.
Titles and `@handles` are mutable convenience labels, not stable identity. Metadata
lifecycle actions (`rename`, `archive`, `restore`) can target managed refs or raw
unmanaged Codex thread ids. `search` uses App Server search for broad discovery, while
`query` uses Dispatch's local indexed managed-history substrate. Attach is metadata-only
by default; use `dispatch sync <selector>` when you want dispatch to refresh its local
indexed view of an attached thread. Sync establishes metadata-only live observation,
indexes recent App Server history first within explicit turn/item/time and
page-checked byte budgets, and persists continuation plus cycle-guard state so
later calls continue older history without starting over or spinning on malformed
provider cursors. If `<selector>` is a raw unmanaged Codex thread id,
`sync` first registers it as an attached read/metadata-managed lane, then refreshes the
index. `dispatch list --unmanaged --archived` shows archived Codex sessions before you
decide whether to sync or restore them. Bare `dispatch history` reads Dispatch's local
index only; selector-scoped transcript reads through `tail`, `history`, or
transcript-inclusive `get` use App Server `thread/read(includeTurns:true)` as the
canonical source and backfill Dispatch's normalized local history index for that one
thread.

Interactive App Server requests use `dispatch request list` and one generic
`dispatch request respond <id> '<json>'` path. Owned threads default to durable
attention; attached/unmanaged requests default to deny. `dispatch schema "request
respond"` exposes the same response contract projected into grouped MCP tools.

History capture is configurable in `~/.dispatch/config.toml`. The default
`standard` mode captures operational facts and bounded searchable history
metadata while keeping raw provider payloads gated. Live App Server events are
stored as compact summaries; transcript reads index bounded turns, item text,
tool names, and file/thread refs without retaining raw item payloads unless the
retention policy allows it. Minimal capture keeps turn-level state but skips
item-level transcript rows. Bare `history` overview is a local indexed summary.
Normal selector-scoped `history` item/tool/file views render from the normalized
index after refreshing one thread from the App Server, while `history --raw`
remains a live raw-payload inspection path. Use `debug` only for development
with bounded temp state; debug retention can store bounded raw provider event
and item payloads with truncation markers for reducer/search diagnosis:

```toml
[history]
capture = "standard" # minimal | standard | debug
raw_payload_retention = "debug" # off | errors | debug | all
max_text_bytes = 8192
max_payload_bytes = 65536
```

`dispatch doctor` reports the active capture mode and warns when debug/raw
retention is enabled.

`new` reports whether the first message was accepted by the App Server, not whether
assistant work completed. Use `get` to inspect the latest turn state and persisted
App Server errors, or `watch` for a bounded live event sample. Slash commands in
`--text` are plain text; use `--goal` when creating a native App Server goal.
Use `dispatch models` before pinning model or service-tier presets; Dispatch
resolves aliases such as `fast` from the live App Server model catalog, accepts
model-defined reasoning efforts, reports input/personality capabilities, and
keeps omitted model/tier values on Codex defaults.
Use `dispatch permissions --cwd /repo` before selecting a named Codex
permission profile. Profiles are project-aware and may be disallowed by
effective requirements. `new --permission-profile <id>` validates the live
catalog before launch; it is mutually exclusive with sandbox, approval-policy,
and approval-reviewer overrides so one authority source remains unambiguous.

For the operator guide, CLI/MCP examples, triggers, and plugin setup, start at
[`docs/usage/README.md`](docs/usage/README.md).

Start troubleshooting with `dispatch doctor`. It checks PATH visibility, the Codex CLI
and auth footprint, daemon socket/pidfile state, registry schema/integrity, packaged
skills/plugin assets, and a low-risk Codex App Server initialize smoke. If doctor reports
an old registry schema, stop the daemon and run `dispatch registry migrate` before
starting it again.

## Agent And Plugin Support

This repo ships first-party skills in [`skills/`](skills/):

- [`skills/dispatch/SKILL.md`](skills/dispatch/SKILL.md) teaches agents how to operate
  dispatch safely.
- [`skills/dm/SKILL.md`](skills/dm/SKILL.md) is the dispatch-backed "dispatch message"
  workflow for short inter-lane messages.

The workspace-local Codex plugin bundle lives at [`plugins/dispatch/`](plugins/dispatch/),
with a marketplace entry in [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json).
Restart Codex if the plugin does not appear immediately.

## Project Docs

- [`docs/development/design.md`](docs/development/design.md) - architecture and design notes.
- [`docs/adrs/`](docs/adrs/) - accepted architecture decisions.
- [`docs/research/`](docs/research/) - verified Codex App Server findings.
- [`.agents/plans/v0/RETRO.md`](.agents/plans/v0/RETRO.md) - v0 execution ledger and
  verification record.

For contributors, [`AGENTS.md`](AGENTS.md) is the canonical fieldguide.
