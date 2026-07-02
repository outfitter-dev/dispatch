# Goal References: local-substrate-stack

## Repo Guidance

- `AGENTS.md` - project commands and source-control/review rules.
- `.claude/rules/python-conventions.md` - async/storage rules if code changes.
- `.claude/rules/contracts.md` - surface derivation rules if CLI/MCP changes.

## Tracker

- `DIS-20` - parent local substrate roadmap.
- `DIS-21` - storage boundary and dual-backend tests.
- `DIS-22` - concurrent event-ingestion harness.
- `DIS-23` - semantic search substrate and retention policy.
- `DIS-24` - multi-machine selected-state sync.
- `DIS-25` - Cloud Gateway route-intent/not-log-sink boundary.

## Source Files

- `docs/development/local-substrate-roadmap.md` - stack roadmap.
- `docs/development/cloud-gateway.md` - gateway boundary.
- `src/outfitter/dispatch/registry/store.py` - current registry/store implementation.
- `tests/registry/` - storage behavior tests.
- `spikes/06_turso_libsql_storage_probe.py` - current backend compatibility probe.
- `src/outfitter/dispatch/registry/sql_compat.py` - representative registry SQL compatibility contract.
- `tests/registry/test_sql_compat.py` - stdlib SQLite compatibility tests.
- `src/outfitter/dispatch/registry/ingest_harness.py` - synthetic event-ingestion harness.
- `scripts/measure_event_ingestion.py` - opt-in ingestion measurement script.
- `tests/registry/test_ingest_harness.py` - harness/script tests.

## Docs / ADRs / Notes

- `docs/adrs/0013-dispatch-mesh-is-daemon-federation.md`
- `docs/adrs/0014-mesh-auth-discovery-and-durable-queues.md`
- `docs/adrs/0023-provider-event-log-and-history-index.md`
- `docs/research/turso-libsql-storage-spike.md`

## Commands

- `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders .agents/goals/2026-07-02-local-substrate-stack/PROMPT.md`
- `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor .agents/goals/2026-07-02-local-substrate-stack`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run python -m pytest tests/registry -q`
- `uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py`
- `uv run pytest tests/registry/test_sql_compat.py -q`
- `uv run pytest tests/registry/test_ingest_harness.py -q`
- `uv run python scripts/measure_event_ingestion.py --events 80 --lanes 4 --concurrency 8 --json`
- `just check`

## PRs / Branches

- `feat/local-substrate-roadmap` - current working branch.
- `dis-21-storage-boundary-contracts` - storage compatibility contract branch.
- `dis-22-event-ingestion-harness` - synthetic event-ingestion harness branch.

## Review Reports

- `.agents/goals/2026-07-02-local-substrate-stack/tmp/reviews/milestone-1-docs.json` - milestone 1 local review, 5/5 clean, no P0/P1/P2.
- `.agents/goals/2026-07-02-local-substrate-stack/tmp/reviews/milestone-2-storage.json` - milestone 2 local review, 5/5 clean, no P0/P1/P2.
- `.agents/goals/2026-07-02-local-substrate-stack/tmp/reviews/milestone-3-ingest.json` - milestone 3 local review, 5/5 clean, no P0/P1/P2.
