# Goal References: Provider Event Log and History Index

Use this as the evidence index for the goal. Prefer short notes with links or
paths over long copied excerpts.

## Repo Guidance

- `AGENTS.md` - project lexicon, command gates, source-control expectations,
  fixture rules, and no-drift surface rules.
- `.claude/rules/contracts.md` - author-once contract projection policy for CLI
  and MCP.
- `.claude/rules/client.md` - App Server access boundary.
- `.claude/rules/python-conventions.md` - async core and Python conventions.

## Tracker

- Linear project `Provider Event Log and History Index` - implementation parent
  for DIS-1 through DIS-10.
- DIS-1 - provider event/history schema and storage boundaries.
- DIS-2 - Codex App Server live events into `provider_events`.
- DIS-3 - reducers for runtime lane state, turns, and receipts.
- DIS-4 - Codex `thread/read` backfill.
- DIS-5 - progressive JSONL sync into normalized history.
- DIS-6 - DB-backed history, search, status, and subscriptions.
- DIS-7 - replay fixtures and gates.
- DIS-8 - Turso/libSQL spike.
- DIS-9 - Claude hook mapping after Codex substrate.
- DIS-10 - docs, skills, and operator guidance.

## Source Files

- `src/outfitter/dispatch/registry/` - durable store, models, and migrations.
- `src/outfitter/dispatch/core/` - handlers, subscriptions, sync, reducers, and
  runtime coordination.
- `src/outfitter/dispatch/client/` - Codex App Server client boundary.
- `src/outfitter/dispatch/contracts/` - op registry and surface projection.
- `tests/fixtures/` - checked-in replay fixtures.
- `tests/scenarios/` - opt-in live workflow fixtures.

## Docs / ADRs / Notes

- `docs/adrs/0023-provider-event-log-and-history-index.md` - primary decision.
- `docs/adrs/0012-codex-id-resolution-and-progressive-sync.md` - sync and id
  resolution context.
- `docs/adrs/0016-daemon-event-log-and-inbox-subscriptions.md` - subscriptions
  and event log context.
- `docs/adrs/0017-provider-runtime-boundary.md` - provider boundary context.
- `docs/adrs/0020-claude-provider-over-pty-and-agent-view.md` - Claude runtime
  constraints.
- `docs/adrs/0021-claude-hooks-for-delivery-and-state-events.md` - Claude hook
  mapping context.
- `docs/adrs/0022-event-subscriptions.md` - subscription behavior.
- `docs/research/app-server-verification.md` - verified Codex App Server
  primitives.
- `docs/usage/` - operator docs to update when behavior changes.
- `skills/dispatch/SKILL.md` - first-party operating skill.
- `skills/dm/SKILL.md` - dispatch-backed direct message skill.

## PRs / Branches

- Branch at packet creation: `docs/inbox-subscriptions-adrs`.
- Implementation branches: pending.
- PRs: pending.

## Commands

- `just check` - repo gate: ruff, format check, mypy strict, pytest.
- `just test` - pytest suite.
- `uv run dispatch --help` - CLI top-level smoke.
- `uv run dispatch schema <command>` - schema smoke for affected commands.
- `git status --short --branch` - worktree state proof.

## Prompt

- `.agents/goals/2026-07-01-provider-event-log-history-index/PROMPT.md` -
  initial direct-start prompt.

## Review Reports

- `.agents/goals/2026-07-01-provider-event-log-history-index/tmp/reviews/` -
  preferred location for local review reports during execution.
