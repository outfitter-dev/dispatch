# Turso/libSQL Decision Memo

Date: 2026-07-02
Status: decision memo

## Decision

Keep SQLite/`aiosqlite` as Dispatch's default local registry/history backend.
Do not migrate the default backend to Turso/libSQL for the next release.

Continue Turso/libSQL as a future optional-backend candidate for three concrete
pressures:

- local vector search over retained/redacted derived artifacts;
- selected multi-machine sync where a local-first database replica is useful;
- measured SQLite write/read contention that cannot be fixed by operation shape,
  batching, indexing, or connection/transaction structure.

Before any production Turso/libSQL path, Dispatch needs a small storage boundary
that can run representative registry/history behavior against both SQLite and
the candidate backend without changing the app-level `Registry` contract.

## Evidence

### Current Local Search Does Not Require Turso

`dispatch query` now searches normalized `thread_items`/`thread_item_refs` for
managed threads only. It is intentionally keyword and structural indexed search,
not semantic/vector search.
The current implementation proves:

- the local query contract can be backed by existing SQLite tables;
- CLI/MCP schema projection can expose a separate local query surface without surface drift;
- local query can preserve `dispatch search` as the App Server broad-search surface;
- retention and embedding policy can be documented before real embeddings exist.

This removes immediate product pressure to introduce vector storage before the
source artifacts, retention rules, and redaction boundary are proven.

### Synthetic Ingestion Baselines Are Stable Enough For Now

The current SQLite/`aiosqlite` registry path handled four synthetic profiles:

| Profile | Command Shape | Result |
| --- | --- | --- |
| small mixed read/write | 100 events, 4 lanes, concurrency 4, reader enabled | 759.102 events/s |
| larger mixed read/write | 500 events, 4 lanes, concurrency 8, reader enabled | 550.824 events/s |
| larger write-only | 500 events, 4 lanes, concurrency 8, no reader | 716.953 events/s |
| raw-retained mixed read/write | 250 events, 4 lanes, concurrency 8, raw retained | 546.005 events/s |

Every profile produced exact totals for provider events, thread turns, thread
items, and message receipts. The mixed read/write profile is slower than
write-only, which is useful signal, but not enough by itself to justify a new
storage engine.

### Turso/libSQL Compatibility Is Promising But Not Production-Ready

The storage spike proved representative registry/history SQL can run on stdlib
SQLite, `pyturso`, and `libsql` after a small conflict-target portability fix.
That is good evidence for future optional-backend work.

It is not enough for a default migration because:

- the production daemon is async and the probed Turso/libSQL Python paths are
  synchronous from Dispatch's perspective;
- full registry tests are still coupled to `aiosqlite.Row`, direct `_conn`
  access, and `aiosqlite` exceptions;
- sync/cloud usage introduces auth, remote URLs, checkpointing, conflict policy,
  and data-ownership decisions;
- vector search still needs source artifact, redaction, retention, deletion, and
  rebuild semantics;
- the baseline data has not shown a correctness or performance cliff in the
  current SQLite path.

## Recommendation

1. Keep SQLite/`aiosqlite` as the default backend.
2. Keep the Turso/libSQL SQL compatibility spike as an early warning gate.
3. Build a small connection/transaction boundary only when it enables real
   second-backend contract tests.
4. Treat vector search as an optional index over normalized retained artifacts,
   not as a reason to store raw transcripts.
5. Evaluate Turso Sync separately from local registry storage when multi-machine
   Dispatch reaches implementation.

## Reopen Conditions

Reopen the default-backend decision when at least one of these is true:

- the ingestion harness or live dogfood data shows SQLite contention that remains
  after batching/indexing/transaction-shape fixes;
- Dispatch commits to local semantic/vector search with a tested embedding and
  retention policy;
- multi-machine sync requires selected database replication and has a credential,
  pairing, conflict, and deletion model;
- Turso/libSQL Python support provides an async path or a proven thread/executor
  integration that passes the registry/history behavior suite;
- packaging proves reliable across Dispatch's supported Python and platform
  matrix.

## Guardrails

- Do not put Turso credentials, remote URLs, or cloud sync settings into the
  default local config path.
- Do not send raw provider payloads, full transcripts, debug captures, secrets,
  or large tool outputs to a remote database by default.
- Do not make semantic search depend on a remote service or paid embedding call
  without explicit operator opt-in.
- Keep SQLite fixtures and behavior tests authoritative until a second backend
  contract suite exists.

## Follow-Up Work

- Add a tiny storage-boundary spike only after a concrete second-backend test
  target is identified.
- Add larger synthetic profiles for debug-sized payloads before broad debug
  retention dogfood.
- Design the first embedding artifact table with provenance, redaction status,
  source row references, dimensions, model id, and rebuild/delete behavior.
- Re-run `spikes/06_turso_libsql_storage_probe.py` whenever registry SQL changes
  in ways that may affect portability.
