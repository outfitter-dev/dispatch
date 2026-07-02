# Turso/libSQL Storage Spike

Date: 2026-07-02
Status: current recommendation

## Summary

Dispatch should not move its default registry/history store from SQLite/`aiosqlite` to Turso/libSQL yet.

The spike did find useful evidence: the current registry schema and representative provider-history writes can run on stdlib SQLite, `pyturso`, and `libsql` after one small SQL portability hardening. That makes Turso/libSQL a plausible future backend candidate. It does not yet justify a default migration because Dispatch's daemon is async, the current Python Turso/libSQL APIs probed locally are synchronous, full registry tests are still coupled to `aiosqlite`, and Turso Sync/cloud semantics need their own security/config story.

Recommendation:

- Keep SQLite/`aiosqlite` as the default local backend.
- Keep Turso/libSQL behind future optional-backend work, not on the default path.
- Use `spikes/06_turso_libsql_storage_probe.py` as an early compatibility gate before storage-boundary changes.
- Revisit Turso when Dispatch has a small connection/transaction boundary, measured SQLite contention, a real semantic-search requirement, or an explicit multi-machine sync product decision.

## Sources Checked

- [Turso libSQL overview](https://docs.turso.tech/libsql)
- [Turso Python quickstart](https://docs.turso.tech/sdk/python/quickstart)
- [Turso Sync usage](https://docs.turso.tech/sync/usage)
- [Turso AI and embeddings](https://docs.turso.tech/features/ai-and-embeddings)
- `docs/adrs/0023-provider-event-log-and-history-index.md`
- `src/outfitter/dispatch/registry/store.py`
- `tests/registry/test_store.py`

## What Changed

The existing provider-event insert used a partial-index-targeted upsert:

```sql
ON CONFLICT(provider, provider_event_id) WHERE provider_event_id IS NOT NULL DO NOTHING
```

That form works on stdlib SQLite and `libsql`, but `pyturso 0.6.1` rejected the tiny reproduction with:

```text
DatabaseError: Parse error: ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint
```

The production query now uses:

```sql
ON CONFLICT DO NOTHING
```

For `provider_events`, that preserves the intended duplicate-event behavior because the only relevant unique conflict is the partial unique index on `(provider, provider_event_id)` where `provider_event_id IS NOT NULL`. A focused regression test verifies that foreign-key violations are still raised rather than silently ignored.

## Probe Results

Command:

```bash
uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py
```

Result:

```text
sqlite3  PASS  schema, upsert, transaction rollback, and summary query worked; partial conflict target supported=True
pyturso  PASS  schema, upsert, transaction rollback, and summary query worked; partial conflict target supported=False
libsql   PASS  schema, upsert, transaction rollback, and summary query worked; partial conflict target supported=True
```

Focused registry checks:

```bash
uv run python -m pytest tests/registry/test_store.py::test_provider_event_history_index_roundtrips_and_dedupes tests/registry/test_store.py::test_provider_event_foreign_key_errors_are_not_ignored -q
uv run python -m pytest tests/registry -q
```

Results: `2 passed`, then `34 passed`.

## Decision Questions

1. What does Turso solve today that SQLite/`aiosqlite` does not after the history-load fixes?
   - Not an urgent correctness gap. SQLite now has WAL, busy timeout, a write lock, transaction-guarded snapshot writes, and DB-backed history overview/details. Turso may help future concurrency, vector search, and sync, but the current problem is mostly storage shape and product direction.

2. Is a small storage boundary possible without a database-framework project?
   - Yes, but it should start below the full `Registry` class. A connection/transaction plus SQL-compatibility boundary is a better first step than carving the entire store into an abstract provider interface.

3. Which Turso path matters first?
   - `pyturso` local embedded matters first for local backend feasibility. `libsql` remote/embedded replicas and Turso Sync matter later for remote/sync products. Vector search is a later optional index.

4. Does the Python API fit the async daemon?
   - Not directly from the local probes. Both `pyturso` and `libsql` exposed synchronous sqlite-like APIs with no obvious async connect. A production integration would need a dedicated thread/executor design or a different async package path.

5. What are the dependency, wheel, platform, and install risks?
   - Local Python 3.13 install/import works for `pyturso 0.6.1` and `libsql 0.1.11`. That is encouraging but not a cross-platform packaging guarantee.

6. Can current registry/fixture tests run against a second backend meaningfully?
   - Representative SQL can run today through the spike. Full parity cannot run yet because tests and production code are coupled to `aiosqlite.Row`, direct `_conn` access, `aiosqlite` exceptions, and SQLite migration seeding.

7. Does vector search belong now?
   - No. Turso/libSQL vector support is relevant, but Dispatch first needs an embedding policy, storage/retention policy, and optional index design over normalized `thread_items` and summaries.

8. Does cloud/sync require security/config scope outside v0?
   - Yes. Turso Sync brings remote URLs, auth tokens, bootstrap choices, conflict policy, checkpointing, and data ownership questions. That is future mesh/cloud-gateway work, not a default local registry change.

9. If deferred, what should reopen the decision?
   - Reopen when Dispatch has a small connection/transaction boundary and one of: measured SQLite write contention under realistic ingest, a committed semantic-search feature needing local vectors, or an explicit multi-machine sync requirement with a credential/config design.

## Follow-Ups

- Add a small storage connection/transaction protocol only when it enables a second backend test without broad churn.
- Keep `spikes/06_turso_libsql_storage_probe.py` updated as registry SQL evolves.
- When semantic search becomes concrete, design embeddings and retention first, then evaluate Turso vector indexes.
- When multi-machine sync becomes concrete, evaluate Turso Sync separately from local registry storage.
