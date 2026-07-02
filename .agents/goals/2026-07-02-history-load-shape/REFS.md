# Goal References: history-load-shape

Use this as the evidence index for the goal. Prefer short notes with links or
paths over long copied excerpts.

## Repo Guidance

- `AGENTS.md` - project commands, lexicon, Graphite, and surface derivation rules.
- `.claude/rules/contracts.md` - author-once op registry rules.
- `.claude/rules/python-conventions.md` - async/Python expectations.

## Tracker

- `DIS-14` - parent: stabilize history and observe load before Turso spike.
- `DIS-15` - DB-only history overview and explicit refresh/backfill.
- `DIS-16` - batch transcript indexing and remove per-item transactions.
- `DIS-17` - registry transaction, WAL, busy-timeout, cancellation safety.
- `DIS-18` - incremental bounded observable sync/history backfill.
- `DIS-19` - safe observe/dogfood command or fixture.
- `DIS-4` - existing backfill issue.
- `DIS-5` - existing progressive JSONL sync issue.
- `DIS-6` - existing DB-backed reads issue.
- `DIS-7` - existing fixture/gate issue.
- `DIS-8` - future Turso/libSQL spike.
- `DIS-10` - docs/skills/operator guidance.

## Source Files

- `src/outfitter/dispatch/core/handlers.py` - `history`, `sync`, and transcript
  handlers.
- `src/outfitter/dispatch/core/history_index.py` - Codex thread/read indexing.
- `src/outfitter/dispatch/core/sync.py` - progressive JSONL sync scanner.
- `src/outfitter/dispatch/registry/store.py` - aiosqlite registry, transactions,
  and history tables.
- `src/outfitter/dispatch/core/history.py` - history projection and rollups.
- `src/outfitter/dispatch/contracts/derive_cli.py` - derived CLI surface.
- `src/outfitter/dispatch/contracts/derive_mcp.py` - derived MCP surface.

## Docs / ADRs / Notes

- `docs/adrs/0023-provider-event-log-and-history-index.md` - event/history
  substrate decision.
- `docs/usage/README.md` - operator documentation to update.
- `skills/dispatch/SKILL.md` - agent-facing operator guidance to update.
- `.agents/notes/dispatch-observer-20260702.sh` - local scratch observer from
  dogfood; not production code.

## PRs / Branches

- `dis-14-stabilize-history-and-observe-load-before-turso-spike` - initial goal
  branch.

## Commands

- `just check` - full repo gate.
- `uv run pytest tests/registry tests/core tests/surfaces -q` - focused broad
  Python test subset for this goal.
- `uv run dispatch history --json` - overview behavior smoke.
- `uv run dispatch schema history --json` - derived schema/help smoke if
  history flags change.
- `uv run dispatch doctor --json` - local runtime/registry health smoke.
- `gt log --no-interactive` - stack state.
- `gh pr checks` / `gh pr view` - remote PR state.

## Prompt

- `.agents/goals/2026-07-02-history-load-shape/PROMPT.md` - initial prompt used
  to start or resume the goal.

## Review Reports

- pending
