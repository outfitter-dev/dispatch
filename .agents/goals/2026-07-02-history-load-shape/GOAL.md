# Goal Execution Contract: history-load-shape

Date: 2026-07-02
Status: Ready
Spec: `.agents/goals/2026-07-02-history-load-shape/SPEC.md`
Prompt: `.agents/goals/2026-07-02-history-load-shape/PROMPT.md`
Retro: `.agents/goals/2026-07-02-history-load-shape/RETRO.md`
Refs: `.agents/goals/2026-07-02-history-load-shape/REFS.md`

## Completion Horizon

`merged`

Complete when:

- DIS-14 through DIS-19 are implemented or explicitly narrowed with recorded
  rationale.
- PR(s) are pushed, reviewed, green, merged to `main`, and local `main` is
  synced clean.
- The final dogfood/regression proof shows the 2026-07-02 history/observe spike
  shape no longer reproduces under the chosen safe path.

Not complete when:

- Fixes are only planned or documented.
- A draft PR exists but is not ready/merged.
- Local checks pass but review still has unresolved P0/P1/P2 findings.
- Turso spike work starts before the SQLite/history shape is stabilized.

## Authority

- May commit: yes, scoped to this goal.
- May push: yes.
- May open PR: yes.
- May mark ready: yes, after local checks and review gate pass.
- May merge: yes, after CI/review/mergeability are green.
- May publish/release: no.
- Needs user approval for: Turso migration/default backend change, PyPI release,
  destructive live registry changes, or any broad live backfill against
  `~/.codex` that is not bounded and explicitly documented.

## Boundary

- In scope: Dispatch repo code, tests, docs, skills, goal packet, Linear issue
  updates/comments, PR descriptions, and safe local dogfood evidence.
- Out of scope: Turso implementation, Claude provider work, remote gateway, PyPI
  publishing, long-lived launchd jobs.
- Do not touch: user secrets, unrelated repos, unrelated branches, unrelated
  live agent state, unbounded live Codex transcript backfills.

## Topology

Packet-backed direct start. Prefer one branch/PR if the diff stays coherent.
Split into stacked PRs only if registry/CLI/observe changes become too large to
review safely together.

## Steps

1. Baseline and reproduce safely
   - Outcome: Current root cause is captured in tests/fixtures or an isolated
     scenario without using live `~/.codex`.
   - Scope: DIS-14, DIS-17, DIS-19.
   - Gate: A focused failing or protective test exists for non-mutating overview
     and transaction/load behavior.

2. DB-only overview and explicit refresh/backfill
   - Outcome: Overview/history filters read indexed data and do not mutate unless
     refresh/backfill is explicit.
   - Scope: DIS-15, DIS-6, DIS-10.
   - Gate: Tests prove no live include-turns read or index mutation for overview.

3. Batched and bounded indexing
   - Outcome: Transcript indexing writes in batches, preserves pruning/capture
     semantics, and exposes bounded/partial facts.
   - Scope: DIS-16, DIS-18, DIS-4, DIS-5.
   - Gate: Large fixture tests pass and show fewer transactions/work units.

4. Registry safety and cancellation
   - Outcome: Transaction helper/guard and SQLite pragma decisions prevent nested
     transactions and improve read/write coexistence.
   - Scope: DIS-17, DIS-1, DIS-7.
   - Gate: Concurrent access tests do not produce nested `BEGIN` errors or daemon
     unresponsiveness.

5. Safe observe/dogfood path and docs
   - Outcome: A safe observe command/scenario/fixture exists, docs and skills
     teach it, and the 2026-07-02 load shape is covered.
   - Scope: DIS-19, DIS-10.
   - Gate: Operator-facing docs explain freshness, refresh/backfill, and safe
     observation.

6. Review, PR, merge, and cleanup
   - Outcome: Checks pass, reviews are clean or only accepted P3s remain, PR(s)
     merge, and local `main` is clean.
   - Scope: all goal issues.
   - Gate: `RETRO.md` final proof is complete.

## Reviews

- Run local review after each substantive milestone or before moving from code
  to PR.
- Use `local-review` style severity: fix all P0/P1/P2; fix easy P3s; record any
  accepted residual P3 with rationale.
- Before merge, perform a full-stack review pass over registry, handlers,
  tests, docs, and skills.

## Evidence Contract

- `RETRO.md` records tracker issue states, branch/PR state, review findings and
  dispositions, verification commands/results, dogfood proof, and final merge
  proof.
- PR description explains the 2026-07-02 root cause and how the fix prevents
  recurrence.
- Linear DIS-14 gets a final comment with PR/check/review/dogfood summary.

## Verification

- Focused tests for registry/history/indexing changes.
- `uv run pytest tests/registry tests/core tests/surfaces -q` or narrower
  commands plus rationale.
- `just check`.
- CLI schema/help smoke for any changed commands, especially `history`,
  `sync`, and observe/backfill surfaces.
- Safe dogfood/regression run against isolated fixtures or bounded live state
  showing no nested transaction error, no surprise broad backfill, and no
  sustained CPU spike.
- Prompt/goal alignment: `PROMPT.md` must carry issues, sequence, checks,
  review loop, stop rules, and merged horizon directly.

## Next Move

- If a check fails: narrow to the smallest reproducer, fix, and rerun focused
  then broad checks.
- If progress stalls: split the branch/PR by milestone and keep the parent goal
  moving.
- If scope is unclear: choose the smaller SQLite/history-shape fix and record
  deferred Turso/provider work.

## Waiting State

- Waiting on: GitHub CI, review bots, or Graphite merge queue after PR submit.
- How to check: `gh pr view`, `gh pr checks`, `gt log --no-interactive`, PR
  review threads.
- Heartbeat cadence: every 10-20 minutes while external checks are pending.
- Continue when: checks are green, review blockers are resolved, and mergeability
  is ready.
- Stop when: CI/review requires credentials or a product decision outside this
  goal.
- Last checked: not started.

## Persistence

- Primary resume surface is this packet, especially `RETRO.md`.
- Secondary resume surfaces are Linear DIS-14 through DIS-19 and PR(s).
- Keep tracker and PR state current whenever implementation diverges from the
  issue text.

## Amendments

`GOAL.md` may be amended when execution reality changes. Record meaningful
changes in `RETRO.md`.

## Stop Rules

- A necessary fix requires changing the default storage backend to Turso.
- A live-state test would require unbounded mutation/backfill of user Codex
  history.
- CI/review/merge requires credentials or approval unavailable to the agent.
- The goal cannot be completed without a product decision about public CLI
  semantics that is not safely inferable.
