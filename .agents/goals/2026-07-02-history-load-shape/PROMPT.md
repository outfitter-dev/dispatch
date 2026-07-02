/goal From `/Users/mg/Developer/outfitter/dispatch`, execute `.agents/goals/2026-07-02-history-load-shape/` to the `merged` horizon.

## Read First
- `AGENTS.md`
- This packet: `SPEC.md`, `GOAL.md`, `REFS.md`
- Linear DIS-14 through DIS-19

## Objective
Fix the SQLite/history operation shape that caused the 2026-07-02 dogfood spike before starting the Turso spike.

## Authority
- May commit, push, open PRs, mark ready, merge, update Linear, and clean up local branch state for this goal.
- Do not publish/release, switch the default backend to Turso, or run unbounded live `~/.codex` backfills.

## Boundary
- In scope: DIS-14..DIS-19; registry/history/sync/observe code; tests; docs; skills; CLI/MCP schema/help; PRs; Linear updates.
- Out of scope: Turso implementation, Claude provider work, remote gateway, PyPI release, long-lived launchd jobs, unrelated repos/branches/state.

## Sequence
1. Baseline/protect: isolated tests or fixtures for non-mutating overview, transaction safety, and load behavior.
2. Make `dispatch history` overview DB-only; make refresh/backfill explicit.
3. Batch transcript indexing; preserve capture/pruning semantics.
4. Harden registry transactions, pragmas, cancellation, and concurrent read/write behavior.
5. Make sync/backfill incremental, bounded, and observable.
6. Add safe observe/dogfood command, scenario, or fixture; update docs/skills.
7. Review, submit PR(s), resolve feedback, merge to `main`, and sync local state.

## Loop
For each milestone: implement the smallest coherent slice, run focused tests, run local review, fix all P0/P1/P2 and easy P3s, update `RETRO.md`, then continue. Use stacked PRs only if the diff becomes too large for one reviewable PR.

## Verification
- Focused tests for changed registry/history/indexing/surface code.
- `uv run pytest tests/registry tests/core tests/surfaces -q` or justified narrower coverage while iterating.
- `just check` before PR readiness.
- CLI/schema smoke for changed `history`, `sync`, and observe/backfill surfaces.
- Safe dogfood/regression proof showing no nested transaction error, no surprise broad backfill, and no sustained dispatchd CPU spike.

## Hard Rules
- `dispatch history` overview must not call live `thread_read(include_turns=True)` or mutate the index unless refresh/backfill is explicit.
- Keep SQLite as the default backend; Turso is a follow-up after this lands.
- Do not run automated tests against live `~/.codex`; live dogfood must be bounded and recorded.
- Preserve derived CLI/MCP contracts from the op registry.

## Stop Rules
- A required fix needs a Turso default-backend change.
- Safe completion needs destructive live data changes or credentials you lack.
- A public CLI semantics decision is genuinely blocked on Matt.

## Definition Of Done
- DIS-14 through DIS-19 are implemented or explicitly narrowed in `RETRO.md`.
- Local review has no unresolved P0/P1/P2; PR(s) are green/reviewed/merged; local `main` is synced clean.
- `RETRO.md` includes checks, review disposition, PR/merge state, Linear state, dogfood proof, and risks.

## Evidence Contract
- Record implementation, checks, reviews, PRs, merge state, Linear updates, and dogfood proof in `RETRO.md`.
- PR descriptions and DIS-14 final comment explain root cause, fixes, checks, and deferred risk.

## Next Move
- If a check fails: reduce to a focused reproducer, fix, rerun focused checks, then broaden.
- If progress stalls: split into stacked PRs by milestone and keep the parent goal moving.
- If scope is unclear: choose the smaller SQLite/history-shape fix and defer Turso/provider work.

## Not Done
- Draft PRs, local-only green checks, docs-only changes, or Turso work are not completion.

## Persistence
- Use `.agents/goals/2026-07-02-history-load-shape/RETRO.md` as the resume surface.
- If waiting on CI/review/merge queue, poll every 10-20 minutes and continue once green.

Keep going until the definition of done is satisfied unless a stop rule fires.
