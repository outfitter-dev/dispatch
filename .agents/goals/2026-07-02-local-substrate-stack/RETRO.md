# Execution Retro: local-substrate-stack

Date started: 2026-07-02
Date finalized: 2026-07-02
Status: complete
Spec: `.agents/goals/2026-07-02-local-substrate-stack/SPEC.md`
Goal: `.agents/goals/2026-07-02-local-substrate-stack/GOAL.md`
Prompt: `.agents/goals/2026-07-02-local-substrate-stack/PROMPT.md`
Refs: `.agents/goals/2026-07-02-local-substrate-stack/REFS.md`

## Summary

- Objective: Land as much of the local substrate stack as safely possible.
- Completion horizon: merged.
- Authority used: Created Linear issues and branch `feat/local-substrate-roadmap`.
- Outcome: merged through milestone 3.
- Tracker/PR/source-control state: Linear `DIS-20` through `DIS-25` created; `DIS-21`, `DIS-22`, and `DIS-25` closed; PRs #59, #60, and #61 merged.
- Verification: packet checks, lint/format, registry tests, mypy, SQL compatibility spike, and synthetic event-ingestion harness passed.
- Review state: milestone 1 through milestone 3 local reviews clean, 5/5, no P0/P1/P2.
- Remaining risks: semantic privacy policy and multi-machine selected-state sync are still open in `DIS-23` and `DIS-24`.

## Readiness

- Prompt checked: pass, 3530/4000 characters, no unresolved placeholders.
- Goal/prompt alignment checked: pass.
- Review blockers: none known.
- Verification blockers: none known.
- Tracker blockers: none known.
- Authority blockers: no release/publish, default backend migration, live data, cloud credentials, or paid API authority.
- Next action: continue with `DIS-23` or `DIS-24` in a separate goal/stack.

## Execution Log

```text
2026-07-02 TBD - Tracker and milestone-1 docs
- Changed: Created Linear parent DIS-20 and children DIS-21 through DIS-25; added roadmap note; clarified Cloud Gateway non-log-sink boundary.
- Verified: `check-goal-prompt --no-placeholders` passed; `goal-loop-doctor` passed; `uv run ruff check .` passed; `uv run ruff format --check .` passed; milestone 1 local review clean 5/5.
- Result: Milestone 1 ready to submit.
- Next: Commit, push/open PR, update Linear, and continue to DIS-21 if safe.
- Blockers: None known.

2026-07-02 TBD - Milestone 2 storage compatibility contract
- Changed: Promoted the representative registry/history SQL compatibility probe into `outfitter.dispatch.registry.sql_compat`; rewired the Turso/libSQL spike to reuse it; added stdlib SQLite contract tests.
- Verified: `uv run pytest tests/registry/test_sql_compat.py -q` passed; `uv run pytest tests/registry -q` passed; `uv run mypy src tests/registry/test_sql_compat.py` passed; focused ruff check/format passed; `uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py` passed for sqlite3, pyturso, and libsql.
- Reviewed: milestone 2 local review clean 5/5; no P0/P1/P2.
- Result: Storage work now has a checked SQL portability target without changing the default SQLite/aiosqlite registry path.
- Next: Commit, submit stacked PR, and update DIS-21.
- Blockers: None known.

2026-07-02 TBD - Milestone 3 synthetic event-ingestion harness
- Changed: Added an opt-in synthetic event-ingestion harness and script; fixed a same-connection transaction race by serializing provider event, message receipt, and runtime-state writes through the registry write lock.
- Verified: `uv run pytest tests/registry/test_ingest_harness.py -q` passed; `uv run pytest tests/registry -q` passed; `uv run mypy src tests/registry/test_ingest_harness.py` passed; focused ruff check/format passed; `uv run python scripts/measure_event_ingestion.py --events 80 --lanes 4 --concurrency 8 --json` wrote 80 provider events, turns, items, and receipts with 19 reader samples at 815.012 events/s.
- Reviewed: milestone 3 local review clean 5/5; no P0/P1/P2.
- Result: Event ingestion now has a repeatable local baseline harness, and the harness found a real registry write-concurrency bug.
- Next: Commit, submit stacked PR, and update DIS-22.
- Blockers: None known.

2026-07-02 TBD - Merge/finalization
- Changed: Marked PRs #59, #60, and #61 ready after local reviews and green CI; merged them through Graphite; synced `main`; closed `DIS-21`, `DIS-22`, and `DIS-25`; commented on parent `DIS-20`.
- Verified: final rebased `just check` passed with ruff, format, mypy, pytest 392 passed / 9 deselected, package build, and package contents check; PR #61 CI and CodeQL completed successfully after merge.
- Result: Local substrate roadmap, SQL compatibility contract, and synthetic event-ingestion harness are landed on `main`.
- Next: `DIS-23` semantic search substrate and `DIS-24` multi-machine selected-state sync remain open.
- Blockers: None known.
```

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-02 TBD | Initial packet created. | Start local substrate stack with milestone review gates. | Matt |

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| milestone-1-docs | roadmap, gateway boundary, goal packet | `.agents/goals/2026-07-02-local-substrate-stack/tmp/reviews/milestone-1-docs.json` | 5/5 | clean | 0 | Docs/tracker milestone only; implementation milestones pending. |
| milestone-2-storage | SQL compatibility contract | `.agents/goals/2026-07-02-local-substrate-stack/tmp/reviews/milestone-2-storage.json` | 5/5 | clean | 0 | Contract probe only; full storage boundary still requires concrete call-site need. |
| milestone-3-ingest | synthetic event-ingestion harness | `.agents/goals/2026-07-02-local-substrate-stack/tmp/reviews/milestone-3-ingest.json` | 5/5 | clean | 0 | Harness found and fixed a registry write-concurrency race. |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |
| `check-goal-prompt --no-placeholders` | goal prompt | pass | 3530/4000 characters; no unresolved placeholders. |
| `goal-loop-doctor` | goal packet | pass | Packet OK. |
| `uv run ruff check .` | repo lint | pass | All checks passed. |
| `uv run ruff format --check .` | formatting | pass | 112 files already formatted. |
| `uv run pytest tests/registry/test_sql_compat.py -q` | storage compatibility contract | pass | 3 passed. |
| `uv run pytest tests/registry -q` | registry tests | pass | 37 passed. |
| `uv run mypy src tests/registry/test_sql_compat.py` | typecheck | pass | Success, no issues found. |
| `uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py` | optional backend spike | pass | sqlite3 PASS; pyturso PASS; libsql PASS. |
| `uv run pytest tests/registry/test_ingest_harness.py -q` | event-ingestion harness | pass | 3 passed. |
| `uv run python scripts/measure_event_ingestion.py --events 80 --lanes 4 --concurrency 8 --json` | synthetic baseline | pass | 80 provider events, turns, items, and receipts; 19 reader samples; 815.012 events/s. |

## Prompt / Goal Alignment

- Checked by: Codex.
- Result: pass.
- Missing from prompt: none after revision.
- Fixes made: Added `Hard Rules`, `Next Move`, `Goal Amendments`, and `Prompt / Goal Alignment` sections required by `goal-loop-doctor`.

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| DIS-20 | created | Parent local substrate roadmap. |
| DIS-21 | done | PR #60 merged; storage SQL compatibility contract landed. |
| DIS-22 | done | PR #61 merged; synthetic event-ingestion harness landed. |
| DIS-23 | created | Semantic search substrate and retention policy. |
| DIS-24 | created | Multi-machine selected-state sync. |
| DIS-25 | done | PR #59 merged; Cloud Gateway route-intent/not-log-sink boundary clarified. |

## Follow-Ups

- Continue beyond the SQL compatibility contract only if the next storage boundary step has a concrete call-site need. Do not introduce a broad storage framework for its own sake.
- Use `scripts/measure_event_ingestion.py` to capture comparable baselines before changing storage engines or transaction shape.
- Keep raw logs/transcripts local by default when designing `DIS-23` and `DIS-24`.

## Final State

- Completion proof: PR #59, PR #60, and PR #61 merged; local `main` synced to `a0407a4`.
- Prompt length: 3530/4000 characters.
- Review report summary: milestone 1, milestone 2, and milestone 3 local reviews were 5/5 with no unresolved P0/P1/P2.
- Verification summary: final rebased `just check` passed with ruff, format, mypy, pytest 392 passed / 9 deselected, package build, and package contents check.
- Forbidden actions audit: no release/publish, live user data ingestion, cloud credential use, default backend migration, or paid API work.
- Remaining P3s / risks: semantic search and multi-machine sync remain open as `DIS-23` and `DIS-24`; the event-ingestion harness is synthetic single-process pressure, not live provider or multi-process proof.
- Final transcript proof: stack merged and synced on 2026-07-02; parent `DIS-20` contains the landed-stack summary.
