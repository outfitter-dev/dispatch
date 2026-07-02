# Event Ingestion Baselines

Date: 2026-07-02
Status: baseline evidence

Dispatch measures event ingestion with synthetic local data before changing the
storage engine or transaction boundary. These profiles use
`scripts/measure_event_ingestion.py`, which writes generated lanes, provider
events, thread turns/items, message receipts, and lane runtime state through the
normal `Registry` APIs.

No live `~/.codex` data, private transcripts, cloud services, or remote Turso
databases are used.

## Environment

- Machine: local macOS development machine.
- Backend: default SQLite/`aiosqlite` registry.
- Database: temporary synthetic database per run.
- Command runner: `uv run python scripts/measure_event_ingestion.py ... --json`.
- Timing: wall-clock elapsed time inside the harness around concurrent writes
  plus optional reader sampling.
- Result snippets omit the local temporary `db_path` field; each run used
  `temporary_db: true`.

These are local development baselines, not production SLOs. Use them to compare
operation shape across future changes, not as absolute performance promises.

## Profiles

### Small Mixed Read/Write

```bash
uv run python scripts/measure_event_ingestion.py --events 100 --lanes 4 --concurrency 4 --json
```

Result:

```json
{"concurrency":4,"elapsed_ms":131.735,"events_per_second":759.102,"events_requested":100,"lanes":4,"raw_retained":false,"reader_enabled":true,"reader_samples":24,"temporary_db":true,"totals":{"message_receipts":100,"provider_events":100,"thread_items":100,"thread_turns":100,"transcript_bytes":1990}}
```

### Larger Mixed Read/Write

```bash
uv run python scripts/measure_event_ingestion.py --events 500 --lanes 4 --concurrency 8 --json
```

Result:

```json
{"concurrency":8,"elapsed_ms":907.731,"events_per_second":550.824,"events_requested":500,"lanes":4,"raw_retained":false,"reader_enabled":true,"reader_samples":115,"temporary_db":true,"totals":{"message_receipts":500,"provider_events":500,"thread_items":500,"thread_turns":500,"transcript_bytes":10390}}
```

### Larger Write-Only

```bash
uv run python scripts/measure_event_ingestion.py --events 500 --lanes 4 --concurrency 8 --no-reader --json
```

Result:

```json
{"concurrency":8,"elapsed_ms":697.396,"events_per_second":716.953,"events_requested":500,"lanes":4,"raw_retained":false,"reader_enabled":false,"reader_samples":0,"temporary_db":true,"totals":{"message_receipts":500,"provider_events":500,"thread_items":500,"thread_turns":500,"transcript_bytes":10390}}
```

### Raw-Retained Mixed Read/Write

```bash
uv run python scripts/measure_event_ingestion.py --events 250 --lanes 4 --concurrency 8 --raw-retained --json
```

Result:

```json
{"concurrency":8,"elapsed_ms":457.871,"events_per_second":546.005,"events_requested":250,"lanes":4,"raw_retained":true,"reader_enabled":true,"reader_samples":58,"temporary_db":true,"totals":{"message_receipts":250,"provider_events":250,"thread_items":250,"thread_turns":250,"transcript_bytes":5140}}
```

## Observations

- The current SQLite/`aiosqlite` path handled all tested synthetic profiles
  without transaction errors after the earlier same-connection race fix.
- Reader sampling has visible cost: the 500-event write-only profile measured
  about 717 events/s, while the comparable mixed read/write profile measured
  about 551 events/s.
- Totals stayed exact across profiles: each requested event produced one provider
  event, one thread turn, one thread item, and one message receipt.
- Raw-retained mode is represented in the profile output, but these synthetic
  payloads are small; this does not measure large debug payload pressure.

## Limits

- Synthetic data is intentionally small and uniform.
- One process and one local registry connection are exercised; this is not a
  multi-process or multi-machine contention test.
- The harness does not replay real App Server event streams.
- Performance varies by machine, filesystem, Python build, and system load.

## Next Uses

- Re-run these exact commands after storage-boundary or transaction-shape changes.
- Add a larger debug-payload profile before enabling broad debug capture in live
  dogfood sessions.
- Use the reader/no-reader delta to evaluate whether future read paths need
  caching, snapshotting, or a separate read connection before considering a new
  storage engine.
