# Goal Spec: history-load-shape

Date: 2026-07-02
Status: Ready

## Objective

Fix Dispatch's DB-backed history and observe operation shape before attempting
the Turso backend spike. The current SQLite path must be boring, bounded, and
reviewed first.

## Context

Dogfooding DB-backed history on 2026-07-02 proved that normalized history is
valuable for cross-thread search, tool/file/thread reference aggregation, and
activity discovery. It also exposed a serious operational shape issue:

- A scratch observer plus broad `dispatch history` reads pushed `dispatchd` to
  roughly 75% CPU.
- A CLI history command timed out while the daemon continued the expensive work.
- SQLite surfaced `cannot start a transaction within a transaction`.
- The registry grew from about 6.5 MB to about 42 MB after broad transcript
  backfill.
- The useful indexed counts afterward proved the substrate is worth keeping:
  thousands of turns/items/refs became queryable.

The lesson is not "use Turso immediately." Turso may help later, but Dispatch
must first stop making read-shaped operations perform surprise live transcript
backfills and per-item transactions.

## Scope

### In

- DIS-14: Stabilize history and observe load before Turso spike.
- DIS-15: DB-only history overview and explicit refresh/backfill semantics.
- DIS-16: Batched transcript indexing instead of per-item transactions.
- DIS-17: Registry transaction guard, WAL/busy-timeout decision, and
  cancellation safety.
- DIS-18: Incremental, bounded, observable sync/history backfill.
- DIS-19: Safe observe/dogfood command or fixture for load regression.
- Tests, docs, skills, CLI/MCP schema/help updates affected by those changes.
- Local review loops until no unresolved P0/P1/P2 findings remain.
- PR submission, review handling, merge to `main`, and local sync.

### Out

- Turso/libSQL/pyturso migration or backend default changes.
- Claude provider/history mapping.
- Remote/cloud gateway work.
- Publishing a PyPI release unless explicitly requested later.
- Broad UI/dashboard work beyond safe CLI/docs/operator surfaces.

## Source Of Truth

- `AGENTS.md` - project rules, commands, lexicon, and Graphite expectations.
- `docs/adrs/0023-provider-event-log-and-history-index.md` - storage direction.
- `src/outfitter/dispatch/core/handlers.py` - current history/sync handlers.
- `src/outfitter/dispatch/core/history_index.py` - transcript indexer.
- `src/outfitter/dispatch/registry/store.py` - SQLite registry and transactions.
- `docs/usage/README.md` - user-facing CLI/history guidance.
- `skills/dispatch/SKILL.md` - agent-facing operator guidance.
- Linear DIS-14 through DIS-19 - tracker scope and acceptance criteria.

## Acceptance Criteria

- `dispatch history` overview is DB-only and non-mutating unless an explicit
  refresh/backfill path is requested.
- Transcript indexing batches writes and avoids per-item transactions.
- Registry write paths cannot overlap manual `BEGIN` transactions on the shared
  connection.
- Long-running history/backfill work is bounded and safe under concurrent reads.
- Sync/backfill reports meaningful skipped/indexed/partial/bounded facts.
- Safe observe/dogfood behavior exists as a command, scenario, or fixture.
- Regression tests cover the 2026-07-02 spike shape.
- Docs and skills warn operators away from broad live backfill and explain the
  safe path.
- `just check` passes.
- Local review finds no unresolved P0/P1/P2 findings.
- PR(s) are merged and local `main` is synced clean.

## Decisions

- Completion horizon is `merged`.
- Use a single direct goal loop with optional stacked branches only if execution
  naturally splits into reviewable slices.
- Keep SQLite/aiosqlite as the default backend for this goal.
- Treat Turso as a follow-up spike after this goal is merged.
- Dogfood live state cautiously; use isolated fixtures for tests and avoid
  pointing automated tests at live `~/.codex`.

## Risks

- Broad live histories are large enough to hide quadratic behavior.
- Transaction fixes may affect many registry methods.
- SQLite pragmas can have migration/runtime consequences; document any choice.
- CLI schema/help must stay derived from the op registry.
- A too-ambitious observe command could expand scope; safe fixture/scenario is
  acceptable if it proves regression coverage.
