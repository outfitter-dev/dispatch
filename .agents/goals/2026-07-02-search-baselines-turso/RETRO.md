# Execution Retro: search-baselines-turso

Date started: 2026-07-02
Date finalized: 2026-07-02
Status: complete

## Summary

- Objective: land local search, ingestion baselines, and Turso decision work.
- Completion horizon: merged.
- Tracker: `DIS-23`, `DIS-26`, `DIS-27`; parent `DIS-20`.
- Outcome: complete; PRs #65-#68 merged to `main`.
- Verification: `just check` passed; CI and CodeQL passed on merged stack PRs.
- Review state: three milestone reviews, all 5/5 clean with no open P0/P1/P2.

## Readiness

- Prompt checked: pass, 2639/4000 characters, no unresolved placeholders.
- Goal/prompt alignment checked: pass.
- Review blockers: none known.
- Verification blockers: none known.
- Tracker blockers: none known.

## Execution Log

```text
2026-07-02 - Preparation
- Created `DIS-26` and `DIS-27` under `DIS-20`.
- Confirmed `0.8.1` GitHub Release and PyPI trusted publishing succeeded.
- Confirmed clean install smoke: `just pypi-smoke -- --package-spec outfitter-dispatch==0.8.1`.
- Created and validated the goal packet. `check-goal-prompt --no-placeholders` passed; `goal-loop-doctor` passed.

2026-07-02 - Milestone 1 local managed-history search
- Added explicit `dispatch search --local` support over normalized registry
  `thread_items` for managed threads.
- Preserved App Server broad search as the default; local search rejects
  `--unmanaged` and does not call App Server `thread/search` or `thread/read`.
- Updated CLI projection, derived schema/help, MCP schema tests, docs, and the
  `dispatch` skill.
- Added `docs/development/semantic-history-search.md` to record the keyword-search
  slice, default exclusions, embedding policy, and storage boundary.
- Local review found one P2 mypy/test-interface issue; fixed before commit.
- `just check` passed after the fix.

2026-07-02 - Milestone 2 event-ingestion baselines
- Added `reader_enabled` and `raw_retained` to ingestion harness JSON output so
  baseline profile dimensions are explicit and test-covered.
- Recorded four synthetic SQLite/`aiosqlite` profiles in
  `docs/research/event-ingestion-baselines.md`.
- Results: small mixed read/write 759.102 events/s; larger mixed read/write
  550.824 events/s; larger write-only 716.953 events/s; raw-retained mixed
  read/write 546.005 events/s.
- Confirmed exact totals for provider events, thread turns, thread items, and
  message receipts in every profile.
- Local review clean after fixing two small P3 evidence-shape issues.

2026-07-02 - Milestone 3 Turso/libSQL decision memo
- Added `docs/research/turso-libsql-decision.md`.
- Decision: keep SQLite/`aiosqlite` as Dispatch's default local backend for the
  next release; keep Turso/libSQL as a future optional backend candidate behind a
  small storage boundary.
- Evidence cited: local keyword search works over existing SQLite tables;
  ingestion baselines did not show an urgent storage-engine cliff; Turso/libSQL
  compatibility is promising but still synchronous from Dispatch's perspective
  and lacks full registry behavior-suite parity.
- Added explicit reopen conditions and guardrails for credentials, remote sync,
  raw payloads, embeddings, and packaging.
- Local review clean with no P0/P1/P2 findings.

2026-07-02 - Merge and tracker closeout
- Merged PR #65, #66, #67, and #68 through Graphite.
- Synced local `main` to `7e41970`.
- Linear auto-marked `DIS-23`, `DIS-26`, and `DIS-27` Done; added completion
  comments to each child issue and a parent rollup comment on `DIS-20`.
```

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-02 | Initial packet created. | Start post-release local search/baseline/Turso loop. | Matt |

## Prompt / Goal Alignment

- Checked by: Codex.
- Result: pass.
- Missing from prompt: none after validation fixes.
- Fixes made: Added standard `## Objective` and `## Verification` sections required by `goal-loop-doctor`; prompt carries sequence, loop, gates, stop rules, and final proof directly.

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| milestone-1 | DIS-23 local managed-history search | `.agents/goals/2026-07-02-search-baselines-turso/tmp/reviews/milestone-1-local-search.json` | 5 | clean | 0 | One P2 mypy/test-interface finding fixed before commit; no open P0-P2. |
| milestone-2 | DIS-26 event-ingestion baselines | `.agents/goals/2026-07-02-search-baselines-turso/tmp/reviews/milestone-2-ingestion-baselines.json` | 5 | clean | 0 | Two P3 evidence-shape issues fixed locally; no open P0-P2. |
| milestone-3 | DIS-27 Turso/libSQL decision memo | `.agents/goals/2026-07-02-search-baselines-turso/tmp/reviews/milestone-3-turso-decision.json` | 5 | clean | 0 | Explicit keep-SQLite-default recommendation; no open P0-P2. |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |
| `just pypi-smoke -- --package-spec outfitter-dispatch==0.8.1` | release | pass | Clean PyPI install smoke passed. |
| `check-goal-prompt --no-placeholders` | goal prompt | pass | 2639/4000 characters; no unresolved placeholders. |
| `goal-loop-doctor` | goal packet | pass | Packet OK. |
| `uv run pytest tests/core/test_handlers.py tests/registry/test_store.py tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_parity.py -q` | DIS-23 focused | pass | 184 passed. |
| `uv run dispatch schema search \| jq -e '.input.properties.local.description, .input.properties.max_scan.description'` | DIS-23 schema smoke | pass | Derived schema exposes `local` and backend-neutral `max_scan` wording. |
| `uv run dispatch search --help \| rg -- '--local\|Search Dispatch'` | DIS-23 help smoke | pass | CLI help exposes `--local`. |
| `just check` | DIS-23 full gate | pass | ruff, format, mypy, pytest 396 passed / 9 deselected, build, and package contents check passed. |
| `uv run pytest tests/registry/test_ingest_harness.py -q` | DIS-26 focused | pass | 3 passed. |
| `uv run mypy src/outfitter/dispatch/registry/ingest_harness.py tests/registry/test_ingest_harness.py` | DIS-26 focused | pass | Success: no issues found in 2 source files. |
| `uv run python scripts/measure_event_ingestion.py --events 100 --lanes 4 --concurrency 4 --json` | DIS-26 baseline | pass | 759.102 events/s; 24 reader samples; exact totals. |
| `uv run python scripts/measure_event_ingestion.py --events 500 --lanes 4 --concurrency 8 --json` | DIS-26 baseline | pass | 550.824 events/s; 115 reader samples; exact totals. |
| `uv run python scripts/measure_event_ingestion.py --events 500 --lanes 4 --concurrency 8 --no-reader --json` | DIS-26 baseline | pass | 716.953 events/s; no reader samples; exact totals. |
| `uv run python scripts/measure_event_ingestion.py --events 250 --lanes 4 --concurrency 8 --raw-retained --json` | DIS-26 baseline | pass | 546.005 events/s; 58 reader samples; exact totals. |
| `git diff --check` | DIS-27 docs review | pass | No whitespace errors. |
| `just check` | final merged stack | pass | ruff, format, mypy, pytest 396 passed / 9 deselected, build, and package contents check passed before each milestone merge. |
| GitHub CI / CodeQL | PR #65 | pass | `check`, `Analyze (actions)`, `Analyze (python)`, and CodeQL succeeded before merge. |
| GitHub CI / CodeQL | PR #66 | pass | Restacked onto `main`; `check`, CodeQL analyses, and Graphite mergeability succeeded before merge. |
| GitHub CI / CodeQL | PR #67 | pass | Restacked onto `main`; `check`, CodeQL analyses, and Graphite mergeability succeeded before merge. |
| GitHub CI / CodeQL | PR #68 | pass | Restacked onto `main`; `check`, CodeQL analyses, and Graphite mergeability succeeded before merge. |

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| DIS-23 | Done | Merged in PR #66: https://github.com/outfitter-dev/dispatch/pull/66 |
| DIS-26 | Done | Merged in PR #67: https://github.com/outfitter-dev/dispatch/pull/67 |
| DIS-27 | Done | Merged in PR #68: https://github.com/outfitter-dev/dispatch/pull/68 |
| PR #65 | merged | Goal packet: https://github.com/outfitter-dev/dispatch/pull/65 |

## Final State

- Completion proof: merged PRs #65-#68; local `main` synced to `7e41970`.
- Review summary: milestone reviews for DIS-23, DIS-26, and DIS-27 are 5/5 clean
  with no open P0/P1/P2.
- Verification summary: focused checks, baseline commands, `goal-loop-doctor`,
  `just check`, GitHub CI, and CodeQL passed.
- Forbidden actions audit: no paid embedding/API calls, no cloud credentials, no
  real transcript embeddings, no backend default migration, no live/private
  benchmark data, and no `DIS-24` implementation.
- Remaining risks: local search is keyword search over indexed managed history;
  baselines are synthetic local measurements; Turso/libSQL remains future
  optional work behind a storage boundary and explicit product trigger.
