# Goal References: App Server Adoption

## Repo Guidance

- `AGENTS.md` - project language, architecture, checks, and source control.
- `.claude/rules/contracts.md` - author-once surface derivation.
- `.claude/rules/client.md` - App Server access boundary.
- `.claude/rules/python-conventions.md` - async and strict typing rules.

## Tracker

- `DIS-41` - parent App Server adoption goal.
- `DIS-42` - interactive server-request completeness.
- `DIS-44` - canonical item ingestion.
- `DIS-45` - parent/descendant topology.
- `DIS-34`, `DIS-35`, `DIS-39` - provider capacity and `dispatch usage`.
- `DIS-18` - bounded resume and backfill.
- `DIS-46` - permission profiles and presets.
- `DIS-47` - rich image inputs.
- `DIS-43` - durable policy follow-up, outside this goal.
- [App Server 0.144 Adoption Plan](https://linear.app/outfitter/document/codex-app-server-0144-adoption-plan-63ab8bff2cd6)
- [Realtime Voice for Dispatch](https://linear.app/outfitter/document/realtime-voice-for-dispatch-0547ab5e24dc)

## Source Files

- `src/outfitter/dispatch/client/` - typed transport, router, events, methods.
- `src/outfitter/dispatch/core/` - handlers, reducers, history, sync, config.
- `src/outfitter/dispatch/contracts/` - authored operations and projections.
- `src/outfitter/dispatch/registry/` - provider/history/runtime persistence.
- `tests/fixtures/app_server/` - checked-in protocol and history fixtures.
- `tests/scenarios/` - isolated live agent workflows.
- `skills/dispatch/SKILL.md` and `skills/dm/SKILL.md` - agent guidance.

## Docs / ADRs / Notes

- `docs/development/design.md` - approved architecture.
- `docs/research/app-server-verification.md` - current protocol findings.
- `docs/adrs/0023-provider-event-log-and-history-index.md` - normalized DB
  substrate.
- `docs/usage/README.md` - primary operator documentation.

## PRs / Branches

- PR #73 `feat/manage-unmanaged-send` - ready, base `main`.
- PR #74 `feat/app-server-0-144` - ready, base #73.
- Packet branch - create as a Graphite slice above #74, then restack/sync after
  the baseline merges.

## Commands

- `just check` - full quality gate.
- `just test-int` - real ephemeral App Server integration tests.
- `just scenario -- tests/scenarios/app_server_adoption.toml` - combined
  isolated live contract to create and exercise during the goal.
- `just app-server-manifest` - regenerate current protocol inventory.
- `uv run dispatch --help`; `uv run dispatch schema usage`, `send`, and `new` -
  CLI/schema projection checks.
- `gt log --no-interactive`, `gh pr checks 73`, `gh pr checks 74`, checks for
  each current milestone PR recorded in `RETRO.md`, and `gt sync` - stack health.
- `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt` - prompt gate.

## Prompt

- `.agents/goals/2026-07-09-app-server-adoption/PROMPT.md` - initial direct-start
  prompt.

## Review Reports

- `.agents/goals/2026-07-09-app-server-adoption/tmp/reviews/` - gitignored local
  targeted and full-stack reports.
