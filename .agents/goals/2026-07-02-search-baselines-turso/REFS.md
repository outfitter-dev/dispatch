# References: search-baselines-turso

## Repo Guidance

- `AGENTS.md`
- `.claude/rules/contracts.md`
- `.claude/rules/python-conventions.md`

## Issues

- `DIS-20` - parent local substrate roadmap.
- `DIS-23` - semantic history search substrate and retention policy.
- `DIS-26` - SQLite event-ingestion baseline profiles.
- `DIS-27` - Turso/libSQL decision memo.

## Source / Docs

- `docs/development/local-substrate-roadmap.md`
- `docs/development/semantic-history-search.md`
- `docs/adrs/0018-top-level-thread-actions-and-search.md`
- `docs/adrs/0023-provider-event-log-and-history-index.md`
- `docs/research/event-ingestion-baselines.md`
- `docs/research/turso-libsql-decision.md`
- `docs/research/turso-libsql-storage-spike.md`
- `src/outfitter/dispatch/core/handlers.py`
- `src/outfitter/dispatch/core/models.py`
- `src/outfitter/dispatch/registry/store.py`
- `scripts/measure_event_ingestion.py`

## Commands

- `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders .agents/goals/2026-07-02-search-baselines-turso/PROMPT.md`
- `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor .agents/goals/2026-07-02-search-baselines-turso`
- `uv run pytest tests/core/test_handlers.py -q`
- `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py -q`
- `uv run python scripts/measure_event_ingestion.py --events 80 --lanes 4 --concurrency 8 --json`
- `just check`
