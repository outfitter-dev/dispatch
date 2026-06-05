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
```

From a source checkout:

```bash
uv sync
uv run dispatch --help
uv run dispatch doctor --no-app-server
uv run dispatch up
uv run dispatch daemon status
```

Open an owned lane, send it work, and inspect the daemon:

```bash
uv run dispatch new \
  --name docs \
  --cwd /path/to/dispatch \
  --text "Please summarize the current stack state."
uv run dispatch lane tail "@[dispatch] docs" --limit 20
uv run dispatch goal set "@[dispatch] docs" "Finish the docs review."
uv run dispatch daemon log --limit 10
uv run dispatch down
```

Use owned lanes for writes. Existing desktop Codex threads can be attached, but v0 treats
attached lanes as observe-only: mutating commands such as `send`, `stop`, `lane archive`,
`goal set`, `goal clear`, `lane fork`, `lane rollback`, and `lane compact` are blocked by
ADR-0005. Attach is metadata-only by default; use `dispatch lane sync <lane>` when you want
dispatch to refresh its local indexed view of an attached thread.

For the operator guide, CLI/MCP examples, triggers, and plugin setup, start at
[`docs/usage/README.md`](docs/usage/README.md).

Start troubleshooting with `dispatch doctor`. It checks PATH visibility, the Codex CLI
and auth footprint, daemon socket/pidfile state, registry schema/integrity, packaged
skills/plugin assets, and a low-risk Codex App Server initialize smoke.

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
