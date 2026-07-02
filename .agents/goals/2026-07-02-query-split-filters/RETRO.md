# Execution Retro: query-split-filters

Date started: 2026-07-02
Date finalized: 2026-07-02
Status: Complete
Spec: `.agents/goals/2026-07-02-query-split-filters/SPEC.md`
Goal: `.agents/goals/2026-07-02-query-split-filters/GOAL.md`
Prompt: `.agents/goals/2026-07-02-query-split-filters/PROMPT.md`
Refs: `.agents/goals/2026-07-02-query-split-filters/REFS.md`

## Summary

- Objective: Split App Server search from local indexed query and add structured local query filters.
- Completion horizon: merged.
- Authority used: implementation, local daemon restart, local review, commit, PR, merge, tracker updates, branch cleanup.
- Outcome: complete and merged.
- Tracker/PR/source-control state: PR #70 merged to `main`; Linear issues `DIS-29` through `DIS-33` moved to review and linked to PR #70.
- Verification: packet checks passed; `just check` passed; live-safe CLI smoke passed after daemon restart.
- Review state: targeted local review scored 5/5 after two P2 findings were fixed.
- Remaining risks: daemon/client version skew remains covered by adjacent `DIS-28`; no public compatibility burden for removed `search --local`.

## Readiness

- Prompt checked: passed, 3810/4000 chars.
- Goal/prompt alignment checked: passed by Codex during setup.
- Review blockers: none known.
- Verification blockers: none known.
- Tracker blockers: none known.
- Authority blockers: none known.
- Next action: keep `DIS-28` as the follow-up daemon/client skew guardrail.

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-02 17:12 ET | Initial goal packet created. | Matt asked to separate query from search, create issues, and set up a goal loop. | Matt |

## Execution Log

```text
2026-07-02 17:12 ET - Prepared tracker and goal packet
- Changed: Created Linear issues DIS-29 through DIS-33 under DIS-20.
- Verified: Repo was on clean main before packet creation.
- Result: Packet ready for checks.
- Next: Run goal-loop prompt and packet checks.
- Blockers: none.

2026-07-02 17:18 ET - Validated goal packet
- Changed: Tightened PROMPT.md after initial length/check failures.
- Verified: `check-goal-prompt --no-placeholders` passed at 3810/4000 chars; `goal-loop-doctor` passed.
- Result: Packet ready for execution.
- Next: Start implementation branch and execute the loop.
- Blockers: none.

2026-07-02 17:45 ET - Implemented query/search split and review fixes
- Changed: Added first-class `query` op, removed `search --local`, added indexed query filters, promoted concrete tool refs, updated CLI/MCP projection tests, docs, and dispatch skill guidance.
- Verified: `just check` passed with ruff, format check, strict mypy, pytest `397 passed / 9 deselected`, package build, and package content check.
- Smoke: `uv run dispatch query --tool linear.save_issue --limit 5 --json` executed through the refreshed daemon and returned no matches; `uv run dispatch query sqlite --limit 3 --json` returned indexed managed-history matches; `uv run dispatch search sqlite --limit 5 --json` returned App Server-backed matches.
- Review: local review found and fixed two P2 issues: `--file-under` substring matching and missing `tool_server` fallback from refs.
- Next: Commit, submit PR, reconcile tracker, merge when checks/reviews permit.
- Blockers: none.

2026-07-02 17:51 ET - Merged implementation and cleaned branch
- Changed: Submitted PR #70, filled in the PR description, marked it ready after CI passed, merged through Graphite, synced `main`, and deleted the merged feature branch locally/remotely.
- Verified: GitHub PR #70 merged at `91a8cba`; CI `check`, CodeQL, and Graphite mergeability were green before merge.
- Result: `main` contains the query/search split.
- Next: Track `DIS-28` separately; no release was published in this goal.
- Blockers: none.
```

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | query/search split and indexed filters | `.agents/goals/2026-07-02-query-split-filters/tmp/reviews/codex/round-1.json` | 5/5 | clean | 0 | Two initial P2 findings fixed with regression coverage. |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |
| `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders .agents/goals/2026-07-02-query-split-filters/PROMPT.md` | prompt | passed | 3810/4000 chars; no unresolved placeholders. |
| `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor .agents/goals/2026-07-02-query-split-filters` | packet | passed | Packet OK; one review report recorded. |
| `uv run dispatch schema search \| jq '.input.properties.local // null'` | CLI schema | passed | Returned `null`; `search` no longer projects `local`. |
| `uv run dispatch schema query \| jq '.input.properties \| keys'` | CLI schema | passed | Query schema exposes structured indexed filters. |
| `uv run dispatch query --tool linear.save_issue --limit 5 --json` | live CLI smoke | passed | Executed through refreshed daemon; no local matches in current registry. |
| `uv run dispatch query sqlite --limit 3 --json` | live CLI smoke | passed | Returned indexed managed-history matches. |
| `uv run dispatch search sqlite --limit 5 --json` | live CLI smoke | passed | Returned App Server-backed matches. |
| `just check` | full repo gate | passed | ruff, format check, mypy, pytest `397 passed / 9 deselected`, build, and package content check passed. |

## Prompt / Goal Alignment

- Checked by: Codex.
- Result: passed.
- Missing from prompt: none after tightening.
- Fixes made: Added required Evidence Contract, Next Move, and Persistence sections; shortened prompt below 4000 chars.

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| DIS-20 | Backlog | Parent local substrate umbrella. |
| PR #70 | Merged | `feat: split local query from app search`; merge commit `91a8cba`. |
| DIS-29 | In Review | Split App Server search from local indexed query; linked to PR #70. |
| DIS-30 | In Review | Add structured query filters; linked to PR #70. |
| DIS-31 | In Review | Promote concrete MCP tool-call metadata; linked to PR #70. |
| DIS-32 | In Review | Unify query/history filter semantics; linked to PR #70. |
| DIS-33 | In Review | Update docs and skills; linked to PR #70. |
| DIS-28 | Backlog | Adjacent daemon/client version-skew issue. |

## Follow-Ups

- `DIS-28` remains separate: the live smoke confirmed stale daemons can report `unknown op 'query'` until restarted.

## Final State

- Completion proof: PR #70 merged to `main` at `91a8cba`; feature branch deleted locally and remotely.
- Prompt length: 3810/4000 chars.
- Review report summary: local review scored 5/5 with no open P0/P1/P2 after two fixed findings.
- Verification summary: `just check`, schema smokes, live query/search smokes, CI, CodeQL, and Graphite mergeability passed.
- Forbidden actions audit: no release/publish, storage-default change, paid/cloud service, secret/config mutation, or raw payload indexing default change.
- Remaining P3s / risks: daemon/client version skew is tracked by `DIS-28`; semantic/vector search and Turso remain out of scope.
- Final transcript proof: this retro records the implementation, review, PR, merge, and cleanup evidence.
