# Goal Spec: History Capture Policy and DB-Backed Surfaces

Date: 2026-07-01
Status: Ready for execution

## Objective

Advance Dispatch's provider event/history substrate from "ingestion is seeded"
to "default capture is useful and operator surfaces can start relying on the
database." Build capture tiers, bounded retention, debug capture, and the first
DB-backed history/search/status cutovers without turning Dispatch into an
unbounded raw transcript warehouse.

## Context

PR #48 introduced the provider event/history substrate and PR #49 layered in
archive-aware sync. The current state intentionally captures a useful subset:
Codex lane lifecycle, turn lifecycle, runtime state, explicit `thread/read`
backfill, archive lifecycle, and basic message receipts.

That is not yet enough for the product we want. Dispatch should capture more
Tier 1 and Tier 2 facts by default, expose a clear capture policy, and provide
a debug mode that can retain richer provider payloads while developing reducers,
search, and future Claude support. At the same time, privacy, storage cost, and
provider-specific volatility must stay explicit.

## Scope

### In

- Capture policy config for `minimal`, `standard`, and `debug` modes.
- Bounded text and payload retention helpers with clear truncation metadata.
- Expanded default capture for Tier 1 operational facts and Tier 2 searchable
  history facts from Codex live events and explicit reads/sync.
- Debug-mode raw provider payload retention for development and reducer replay.
- Doctor/status visibility for capture mode and raw retention posture.
- First DB-backed read/search/status shifts where freshness is honest and tests
  can prove correctness.
- CLI/MCP/schema parity for any changed surface behavior.
- ADR/docs/usage/skill updates that explain capture tiers, retention, debug
  mode, privacy posture, and DB-backed behavior.
- Linear issue updates or new follow-up issues when existing DIS issues do not
  cover discovered scope.

### Out

- Making Turso/libSQL the default store.
- Full raw streaming delta capture by default.
- Automatic whole-home unmanaged thread indexing by default.
- Claude provider ingestion beyond follow-up issues or design notes.
- Merge, release, publish, or package upload.
- Mutating user global Codex/Claude config or using live user state as a test
  fixture.

## Source Of Truth

- `AGENTS.md`
- `docs/adrs/0023-provider-event-log-and-history-index.md`
- `docs/adrs/0017-progressive-thread-sync-index.md`
- `docs/adrs/0018-top-level-thread-actions-and-search.md`
- `docs/usage/README.md`
- `skills/dispatch/SKILL.md`
- `skills/dm/SKILL.md`
- `src/outfitter/dispatch/registry/store.py`
- `src/outfitter/dispatch/core/event_index.py`
- `src/outfitter/dispatch/core/history_index.py`
- `src/outfitter/dispatch/core/handlers.py`
- `tests/fixtures/`
- `tests/scenarios/`

## Acceptance Criteria

- Capture modes are configurable, documented, tested, and visible through an
  operator-facing status or doctor path.
- Standard mode captures more Tier 1 and Tier 2 facts by default while keeping
  raw/heavy payloads bounded or off.
- Debug mode captures richer raw provider payloads and reducer evidence with
  explicit size limits and clear warnings.
- At least one meaningful history/status/search surface reads from the DB when
  freshness is sufficient, with live refresh/fallback behavior documented.
- Tests prove retention bounds, truncation metadata, standard-vs-debug behavior,
  reducer idempotency, and DB-backed read correctness.
- CLI/MCP/schema projections remain derived from op contracts.
- Docs and skills are updated for user-facing behavior.
- Each milestone has a local-review loop before moving up the stack.
- Final stack is pushed as ready PRs with green checks and no unresolved P0/P1/P2
  findings.

## Decisions

- Completion horizon is `ready-pr`.
- Topology is a stacked PR goal above the packet branch, which sits above
  `feat/archive-aware-sync`.
- SQLite/`aiosqlite` remains the default store.
- Capture is tiered: operational facts always, normalized searchable history by
  default, raw/debug payloads only behind explicit policy.
- Live scenarios are optional and must use isolated `DISPATCH_HOME`/`CODEX_HOME`
  or safe read-only App Server paths.

## Risks

- Capturing too much raw data by default could store secrets or large private
  context.
- Capturing too little would leave `history` and `search` dependent on slow,
  provider-specific reads.
- DB-backed surfaces can lie if freshness, sync state, and fallback behavior are
  not explicit.
- A broad stack can drift; milestone review loops are required before moving
  forward.
