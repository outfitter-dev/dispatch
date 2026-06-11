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
uv run dispatch up --json
uv run dispatch daemon status
```

Create an owned managed thread, send it work, and inspect the daemon:

```bash
uv run dispatch new \
  --name docs \
  --cwd /path/to/dispatch \
  --goal "Finish the docs review." \
  --text "Please summarize the current stack state."
uv run dispatch list
uv run dispatch get <dispatch-ref>
uv run dispatch tail <dispatch-ref> --limit 20
uv run dispatch daemon log --limit 10
uv run dispatch down --json
```

Use owned managed threads for turn-writing work. Existing desktop Codex threads can be attached as
managed threads, but ADR-0005 still blocks turn-writing and history-mutating commands such
as `send`, `stop`, `goal set`, and `goal clear` on attached lanes. Every managed
thread has a dispatch-local `ref`; full Codex thread UUIDs remain accepted everywhere.
Titles and `@handles` are mutable convenience labels, not stable identity. Metadata
lifecycle actions (`rename`, `archive`, `restore`) can target managed refs or raw
unmanaged Codex thread ids, and `search` can span both. Attach is metadata-only by
default; use `dispatch sync <selector>` when you want dispatch to refresh its local
indexed view of an attached thread.

`new` reports whether the first message was accepted by the App Server, not whether
assistant work completed. Use `get` to inspect the latest turn state and persisted
App Server errors, or `watch` for a bounded live event sample. Slash commands in
`--text` are plain text; use `--goal` when creating a native App Server goal.
Use `dispatch models` before pinning model or service-tier presets; Dispatch
resolves aliases such as `fast` from the live App Server model catalog and keeps
omitted model/tier values on Codex defaults.

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
