# Execution Retro: History Capture Policy and DB-Backed Surfaces

Date started: 2026-07-01
Date finalized: not finalized
Status: Ready for direct start
Spec: `.agents/goals/2026-07-01-history-capture-policy/SPEC.md`
Goal: `.agents/goals/2026-07-01-history-capture-policy/GOAL.md`
Prompt: `.agents/goals/2026-07-01-history-capture-policy/PROMPT.md`
Refs: `.agents/goals/2026-07-01-history-capture-policy/REFS.md`

## Summary

- Objective: Prepare and execute a stacked PR goal for capture tiers, standard
  history capture, debug capture, and first DB-backed operator surfaces.
- Completion horizon: `ready-pr`.
- Topology: packet-backed direct execution with milestone Graphite stack.
- Current base: packet branch `docs/history-capture-policy-goal` should sit
  above `feat/archive-aware-sync`, which is stacked on PR #48/#49.
- Authority: commit, push, PR, mark ready, tracker updates, bounded subagents,
  and isolated local scenarios are allowed; merge, release, publish, storage
  default changes, and live user state mutation are not allowed.
- Current state: Milestone 1 is complete and Milestone 2 implementation is
  ready for local review.

## Readiness

- Prompt checked: passed at 3873 characters.
- Goal/prompt alignment checked: passed.
- Review blockers: Milestone 2 local review pending.
- Verification blockers: none known.
- Tracker blockers: none known before implementation.
- Authority blockers: merge/release/publish/storage-default changes require
  explicit approval and are out of scope.
- Next action: commit Milestone 2 and run standing plus targeted local review.

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-01 America/New_York | Initial packet created with ready-pr horizon and stacked-PR topology. | Matt requested an ambitious goal-loop for capture policy, default capture expansion, debug capture, and DB-backed history/search/status work. | Matt |

## Execution Log

```text
2026-07-01 America/New_York - Preparation
- Changed: Created SPEC.md, GOAL.md, PROMPT.md, REFS.md, and RETRO.md.
- Verified: `check-goal-prompt` passed at 3873/4000 characters,
  `check-goal-prompt --no-placeholders` passed, and `goal-loop-doctor` passed.
- Result: Packet ready for direct goal start.
- Next: Start the goal from `PROMPT.md`.
- Blockers: None known.

2026-07-01 America/New_York - Milestone 1 capture policy foundation
- Changed: Created `feat/history-capture-policy` above the packet branch. Added
  `CapturePolicy` config for `minimal|standard|debug`, raw payload retention
  policy, byte caps, bounded capture helpers, daemon context injection, doctor
  capture-policy diagnostics, README/usage/skill docs, and focused tests.
- Verified: focused config/capture/doctor/registry/fixture tests passed; ruff
  passed on touched files; mypy passed on touched source; doctor JSON smoke
  showed `capture_policy` with standard mode and bounded defaults.
- Result: Milestone 1 implementation is ready for local review.
- Next: Commit the milestone and run standing plus targeted local-review.
- Blockers: None known.

2026-07-01 America/New_York - Milestone 1 review fixes
- Changed: Fixed four P2 review findings: error-only raw retention now has
  explicit retention semantics and doctor warning visibility; invalid capture
  byte caps fail instead of widening to defaults; bounded payload retention now
  caps the final serialized retained payload; default capture-policy tests now
  isolate Dispatch config/env from live operator state.
- Verified: focused config/capture/doctor tests passed; broader milestone
  config/capture/doctor/registry/fixture tests passed; ruff and mypy passed on
  touched source; doctor smokes confirmed `errors` warns and invalid byte caps
  fail.
- Result: Ready for the same milestone reviewers to re-check.
- Next: Amend the milestone commit and rerun standing plus targeted reviews.
- Blockers: None known.

2026-07-01 America/New_York - Milestone 1 format review fix
- Changed: Ran Ruff formatter on the files Nash identified in targeted
  re-review.
- Verified: broader milestone tests passed; ruff check passed; ruff format
  check passed; mypy passed on touched source.
- Result: Targeted re-review formatting P2 is fixed locally.
- Next: Amend the milestone commit and request targeted re-review.
- Blockers: None known.

2026-07-01 America/New_York - Milestone 1 closed
- Changed: Final targeted re-review passed after formatting fix.
- Verified: Standing re-review clean 5/5 with 0 open P0-P2; targeted final
  re-review clean 5/5 with 0 open P0-P2.
- Result: Milestone 1 capture policy foundation is complete.
- Next: Create `feat/history-standard-capture` and begin standard capture
  expansion.
- Blockers: None known.

2026-07-01 America/New_York - Milestone 2 standard capture expansion
- Changed: Created `feat/history-standard-capture` above
  `feat/history-capture-policy`. Wired live provider-event indexing and
  `thread/read` backfill through `Ctx.capture`; enriched compact event
  summaries; bounded provider failure text in event, turn, and runtime-state
  reductions; stopped default transcript backfill from retaining raw item
  payloads; retained bounded raw item payloads only when the policy allows it
  for error/debug/all modes; updated README, usage docs, and the dispatch skill.
- Verified: focused handler/reactor/capture/config/doctor tests plus relevant
  registry/fixture tests passed; ruff check, ruff format check, and mypy passed
  on touched code/tests.
- Result: Milestone 2 implementation is ready for local review.
- Next: Commit the milestone and run standing plus targeted reducer/storage
  review.
- Blockers: None known.

2026-07-01 America/New_York - Milestone 2 review fixes
- Changed: Fixed standing P2 LR-201 by bounding live `TurnFailed` text before
  writing `lanes.latest_error`; also bounded direct/queued turn-request failure
  errors before writing lane latest-error state and message receipts. Closed
  targeted P2 coverage gaps with tests for debug/all oversized raw-retention
  storage, reactor-level live-event dedupe, and `history`/`transcript`/`show`
  capture-policy wiring.
- Verified: focused reactor/handler tests passed; broader milestone tests
  passed; ruff check, ruff format check, and mypy passed on touched code/tests.
- Result: Milestone 2 review findings are fixed locally.
- Next: Amend the milestone commit and rerun standing plus targeted re-review.
- Blockers: None known.

2026-07-01 America/New_York - Milestone 2 closed
- Changed: Standing and targeted re-reviews both passed clean after the
  review-fix amend.
- Verified: Standing re-review clean 5/5 with 0 open P0-P2; targeted
  reducer/storage re-review clean 5/5 with 0 open P0-P2.
- Result: Milestone 2 standard capture expansion is complete.
- Next: Create `feat/history-debug-capture` and begin debug payload retention.
- Blockers: None known.
```

## Branch / PR Log

| Branch | Base | PR | State | Notes |
| --- | --- | --- | --- | --- |
| `feat/history-capture-policy` | `docs/history-capture-policy-goal` | pending | local milestone complete | Capture policy foundation. |
| `feat/history-standard-capture` | `feat/history-capture-policy` | pending | local milestone complete | Standard Tier 1/Tier 2 capture. |
| `feat/history-debug-capture` | `feat/history-standard-capture` | pending | not started | Debug retention mode. |
| `feat/db-backed-history-surfaces` | `feat/history-debug-capture` | pending | not started | DB-backed history/search/status surfaces. |

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| preparation | packet shape | not applicable | not scored | passed | 0 | Prompt and doctor checks passed. |
| milestone-1 round-1 | capture policy foundation | `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-1/standing.json`; `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-1/targeted-config-privacy.json` | 3 / 3 | changes_requested | 4 | Four unique P2s fixed locally; re-review pending. |
| milestone-1 round-2 | capture policy foundation | `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-1/standing-rereview.json`; `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-1/targeted-config-privacy-rereview.json` | 5 / 3 | changes_requested | 1 | Standing clean; targeted found Ruff format P2, fixed locally. |
| milestone-1 final | capture policy foundation | `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-1/targeted-config-privacy-final.json` | 5 | clean | 0 | Milestone 1 closed. |
| milestone-2 pre-review | standard capture expansion | not applicable | not scored | implementation ready | 0 | Review requests pending. |
| milestone-2 round-1 | standard capture expansion | `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-2/standing.json`; `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-2/targeted-reducer-storage.json` | 3 / 3 | changes_requested | 4 | Standing found one P2 bounded-error bypass; targeted found three P2 test gaps. All fixed locally; re-review pending. |
| milestone-2 final | standard capture expansion | `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-2/standing-rereview.json`; `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/milestone-2/targeted-reducer-storage-rereview.json` | 5 / 5 | clean | 0 | Milestone 2 closed. |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |
| `check-goal-prompt PROMPT.md` | prompt length | passed | 3873 characters under 4000. |
| `check-goal-prompt --no-placeholders PROMPT.md` | prompt placeholders | passed | No unresolved placeholders. |
| `goal-loop-doctor .agents/goals/2026-07-01-history-capture-policy` | packet readiness | passed | Packet OK. |
| `uv run pytest tests/test_config.py tests/core/test_capture.py tests/test_doctor.py tests/registry/test_store.py tests/fixtures/test_corpus.py -q` | milestone 1 focused tests | passed | 56 passed. |
| `uv run ruff check src/outfitter/dispatch/config.py src/outfitter/dispatch/core/capture.py src/outfitter/dispatch/contracts/context.py src/outfitter/dispatch/daemon/host.py src/outfitter/dispatch/doctor.py tests/test_config.py tests/core/test_capture.py tests/test_doctor.py tests/fakes.py` | milestone 1 lint | passed | All checks passed. |
| `uv run mypy src/outfitter/dispatch/config.py src/outfitter/dispatch/core/capture.py src/outfitter/dispatch/contracts/context.py src/outfitter/dispatch/daemon/host.py src/outfitter/dispatch/doctor.py` | milestone 1 types | passed | No issues found. |
| `DISPATCH_HOME=/tmp/dispatch-capture-doctor uv run dispatch doctor --no-app-server --json \| jq '.checks[] \| select(.name == "capture_policy")'` | doctor smoke | passed | Reported standard mode, raw retention `debug`, raw payloads disabled, and 8192/65536 byte caps. |
| `uv run pytest tests/test_config.py tests/core/test_capture.py tests/test_doctor.py -q` | milestone 1 review fixes | passed | 27 passed. |
| `uv run pytest tests/test_config.py tests/core/test_capture.py tests/test_doctor.py tests/registry/test_store.py tests/fixtures/test_corpus.py -q` | milestone 1 broader recheck | passed | 61 passed. |
| `uv run ruff check src/outfitter/dispatch/config.py src/outfitter/dispatch/core/capture.py src/outfitter/dispatch/doctor.py tests/test_config.py tests/core/test_capture.py tests/test_doctor.py` | milestone 1 review-fix lint | passed | All checks passed. |
| `uv run mypy src/outfitter/dispatch/config.py src/outfitter/dispatch/core/capture.py src/outfitter/dispatch/doctor.py` | milestone 1 review-fix types | passed | No issues found. |
| `DISPATCH_HOME=/tmp/dispatch-capture-errors DISPATCH_RAW_PAYLOAD_RETENTION=errors uv run dispatch doctor --no-app-server --json \| jq '.checks[] \| select(.name == "capture_policy")'` | error-retention doctor smoke | passed | Reported warning, `raw_payload_retention=errors`, and raw retention enabled. |
| `DISPATCH_HOME=/tmp/dispatch-capture-invalid DISPATCH_CAPTURE_MAX_PAYLOAD_BYTES=0 uv run dispatch doctor --no-app-server --json \| jq '.checks[] \| select(.name == "capture_policy")'` | invalid-cap doctor smoke | passed | Reported capture policy failure with `history.max_payload_bytes must be a positive integer`. |
| `uv run ruff format src/outfitter/dispatch/config.py src/outfitter/dispatch/core/capture.py tests/test_config.py tests/test_doctor.py` | milestone 1 format fix | passed | 4 files reformatted. |
| `uv run ruff check ... && uv run ruff format --check ...` | milestone 1 format recheck | passed | All checks passed; 4 files already formatted. |
| `uv run pytest tests/core/test_triggers.py tests/core/test_handlers.py tests/core/test_capture.py tests/test_config.py tests/test_doctor.py -q` | milestone 2 focused tests | passed | 145 passed. |
| `uv run pytest tests/core/test_triggers.py tests/core/test_handlers.py tests/core/test_capture.py tests/test_config.py tests/test_doctor.py tests/registry/test_store.py tests/fixtures/test_corpus.py -q` | milestone 2 broader tests | passed | 179 passed. |
| `uv run ruff check src/outfitter/dispatch/core/event_index.py src/outfitter/dispatch/core/history_index.py src/outfitter/dispatch/core/reactor.py src/outfitter/dispatch/core/handlers.py tests/core/test_triggers.py tests/core/test_handlers.py` | milestone 2 lint | passed | All checks passed. |
| `uv run ruff format --check src/outfitter/dispatch/core/event_index.py src/outfitter/dispatch/core/history_index.py src/outfitter/dispatch/core/reactor.py src/outfitter/dispatch/core/handlers.py tests/core/test_triggers.py tests/core/test_handlers.py` | milestone 2 format | passed | 6 files already formatted. |
| `uv run mypy src/outfitter/dispatch/core/event_index.py src/outfitter/dispatch/core/history_index.py src/outfitter/dispatch/core/reactor.py src/outfitter/dispatch/core/handlers.py tests/core/test_triggers.py tests/core/test_handlers.py` | milestone 2 types | passed | No issues found. |
| `uv run pytest tests/core/test_triggers.py tests/core/test_handlers.py -q` | milestone 2 review-fix focused tests | passed | 125 passed. |
| `uv run pytest tests/core/test_triggers.py tests/core/test_handlers.py tests/core/test_capture.py tests/test_config.py tests/test_doctor.py tests/registry/test_store.py tests/fixtures/test_corpus.py -q` | milestone 2 review-fix broader tests | passed | 186 passed. |
| `uv run ruff check src/outfitter/dispatch/core/event_index.py src/outfitter/dispatch/core/history_index.py src/outfitter/dispatch/core/reactor.py src/outfitter/dispatch/core/handlers.py src/outfitter/dispatch/core/queue.py tests/core/test_triggers.py tests/core/test_handlers.py` | milestone 2 review-fix lint | passed | All checks passed. |
| `uv run ruff format --check src/outfitter/dispatch/core/event_index.py src/outfitter/dispatch/core/history_index.py src/outfitter/dispatch/core/reactor.py src/outfitter/dispatch/core/handlers.py src/outfitter/dispatch/core/queue.py tests/core/test_triggers.py tests/core/test_handlers.py` | milestone 2 review-fix format | passed | 7 files already formatted. |
| `uv run mypy src/outfitter/dispatch/core/event_index.py src/outfitter/dispatch/core/history_index.py src/outfitter/dispatch/core/reactor.py src/outfitter/dispatch/core/handlers.py src/outfitter/dispatch/core/queue.py tests/core/test_triggers.py tests/core/test_handlers.py` | milestone 2 review-fix types | passed | No issues found. |

## Prompt / Goal Alignment

- Checked by: Codex.
- Result: passed.
- Missing from prompt: none known.
- Fixes made: Added required section headings and trimmed prompt under 4000
  characters.

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| Dispatch Linear state | not changed during preparation | Executor may update or create issues if existing DIS issues do not cover discovered work. |
| PR #48 | existing lower stack | Provider event history substrate. |
| PR #49 | existing top stack | Archive-aware sync. |

## Follow-Ups

- None yet.

## Final State

- Not finalized.
