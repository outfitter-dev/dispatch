# Execution Retro: search-baselines-turso

Date started: 2026-07-02
Date finalized: pending
Status: active

## Summary

- Objective: land local search, ingestion baselines, and Turso decision work.
- Completion horizon: merged.
- Tracker: `DIS-23`, `DIS-26`, `DIS-27`; parent `DIS-20`.
- Outcome: pending.
- Verification: pending.
- Review state: pending.

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

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| DIS-23 | local implementation ready | Local keyword-search substrate and retention/embedding policy complete locally; PR pending. |
| DIS-26 | local implementation ready | Baseline note complete locally; PR pending. |
| DIS-27 | todo | Turso/libSQL decision memo. |

## Final State

- Completion proof: pending.
- Review summary: pending.
- Verification summary: pending.
- Forbidden actions audit: pending.
- Remaining risks: pending.
