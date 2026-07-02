# Goal References: turso-libsql-storage-spike

Use this as the evidence index for the goal. Prefer short notes with links or paths over long copied excerpts.

## Repo Guidance

- `AGENTS.md` - Dispatch project guidance, commands, lexicon, and contract/surface rules.
- `.claude/rules/contracts.md` - author-once/derive-surfaces rule if CLI/MCP guidance becomes relevant.
- `.claude/rules/python-conventions.md` - async core and testing conventions if production storage code changes.

## Tracker

- `DIS-8` - Linear issue for the Turso/libSQL spike.

## Source Files

- `src/outfitter/dispatch/registry/store.py` - current SQLite/`aiosqlite` store, migrations, transaction helpers, and history tables.
- `src/outfitter/dispatch/registry/models.py` - registry row models at the storage boundary.
- `src/outfitter/dispatch/config.py` - registry path/config defaults.
- `tests/registry/` - current storage tests and migration behavior.
- `tests/fixtures/` - fixture conventions; prefer builders over committed SQLite databases.
- `spikes/06_turso_libsql_storage_probe.py` - committed compatibility probe for current registry SQL against stdlib SQLite, `pyturso`, and `libsql`.

## Docs / ADRs / Notes

- `docs/adrs/0023-provider-event-log-and-history-index.md` - current decision: keep SQLite default, spike Turso/libSQL behind a boundary.
- `docs/research/turso-libsql-storage-spike.md` - 2026-07-02 spike result and final recommendation.
- `.agents/goals/2026-07-02-history-load-shape/RETRO.md` - prior stabilization proof; Turso can start after this.
- `.agents/goals/2026-07-01-provider-event-log-history-index/RETRO.md` - prior provider event/history index proof and follow-up scope.
- `.agents/goals/2026-07-01-history-capture-policy/RETRO.md` - storage/privacy/capture-policy proof and storage-bound review history.

## External Docs

- `https://docs.turso.tech/libsql` - libSQL and Turso Database positioning, SQLite compatibility, vector-search overview.
- `https://docs.turso.tech/sdk/python/quickstart` - current Python package guidance for `pyturso`, `libsql`, and sync examples.
- `https://docs.turso.tech/sync/usage` - Turso Sync push/pull, bootstrap, checkpoint, stats, and offline-first behavior.
- `https://docs.turso.tech/features/ai-and-embeddings` - native vector types, functions, and index/query model.

## PRs / Branches

- `main` at `c00fb18` - base state before this goal.
- `dis-8-turso-libsql-storage-spike` - working branch for this goal.
- PR #57 - https://github.com/outfitter-dev/dispatch/pull/57

## Commands

- `uv run python -m pytest tests/registry -q` - focused storage regression gate.
- `uv run python -m pytest tests/fixtures tests/registry -q` - fixture plus storage gate if fixture builders change.
- `uv run --with pyturso python -c "import turso; print('pyturso-ok')"` - passed locally; installed/imported `pyturso 0.6.1` on Python 3.13.
- `uv run --with libsql python -c "import libsql; print('libsql-ok')"` - passed locally; installed/imported `libsql 0.1.11` on Python 3.13.
- `uv run --with pyturso python - <<'PY'` - local embedded API probe; `turso.connect(':memory:')` supports sqlite-like synchronous methods and no obvious async connect.
- `uv run --with libsql python - <<'PY'` - local API probe; `libsql.connect(':memory:')` supports synchronous methods, cursor `fetchall`, and no obvious async connect.
- `uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py` - passed after SQL portability hardening; `pyturso` still reports partial conflict target support as false.
- `uv run python -m pytest tests/registry/test_store.py::test_provider_event_history_index_roundtrips_and_dedupes tests/registry/test_store.py::test_provider_event_foreign_key_errors_are_not_ignored -q` - passed, provider-event dedupe and FK behavior.
- `uv run python -m pytest tests/registry -q` - passed, 34 registry tests.
- `just check` - repo gate before ready/merge.
- `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders .agents/goals/2026-07-02-turso-libsql-storage-spike/PROMPT.md` - prompt concreteness gate.
- `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor .agents/goals/2026-07-02-turso-libsql-storage-spike` - packet consistency gate.

## Prompt

- `.agents/goals/2026-07-02-turso-libsql-storage-spike/PROMPT.md` - initial prompt used to start or resume the goal.

## Review Reports

- `.agents/goals/2026-07-02-turso-libsql-storage-spike/tmp/reviews/full-stack-round-1.json` - local full-stack review, 5/5 clean, no P0/P1/P2.
