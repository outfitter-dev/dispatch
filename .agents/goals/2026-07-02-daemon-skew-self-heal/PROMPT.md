/goal From `/Users/mg/Developer/outfitter/dispatch`, execute `.agents/goals/2026-07-02-daemon-skew-self-heal` to the merged horizon.

## Objective
Implement `DIS-28` so a newer CLI detects stale `dispatchd` op/version skew, restarts and retries once when the daemon is provably idle, and gives a clear recovery when restart is unsafe.

## Authority
Commit, push, open PR, mark ready, merge, and update Linear. Do not publish/release. Ask before changing LaunchAgent policy, restarting busy daemons by default, storage defaults, or user-level config.

## Boundary
Touch control metadata, CLI lifecycle/retry behavior, tests, docs, skill guidance, and the goal retro. Do not touch remote mesh, App Server internals, live thread contents beyond read-only smoke, secrets, or unrelated roadmap work.

## Sequence
1. Metadata/skew detection.
2. Guarded restart/retry.
3. Tests/docs/review.
4. PR/merge/tracker reconciliation.

## Loop
1. Add metadata/skew detection so missing current-CLI ops are recognized as daemon/client skew, not ordinary user errors.
2. Add guarded self-heal: idle daemon restart + one retry; busy/uncertain daemon prints a recovery command and does not restart silently.
3. Add tests for metadata, idle self-heal, busy no-self-heal, restart failure, and no infinite retry.
4. Update docs/skills/retro.
5. Run focused tests, `just check`, local review, live-safe smoke, PR/CI, merge, sync clean `main`, close `DIS-28`.

## Hard Rules
Preserve author-once surface derivation. Never restart busy daemons silently. Never hide unknown-op typos as skew unless the op exists in the current CLI registry. Lifecycle helpers must not signal unrelated processes.

## Stop Rules
Stop if safe idle detection requires a broader daemon-state redesign, if a policy decision is needed, or if implementation would restart busy daemons silently.

## Definition Of Done
Merged to `main`, local clean, PR CI green, Linear `DIS-28` Done, retro finalized with evidence.

## Evidence Contract
PR body and `RETRO.md` include behavior, tests, local review, live smoke, PR/merge state, and remaining risks.

## Next Move
If checks fail, reproduce narrowly and fix the smallest cause. If scope is unclear, choose conservative no-restart behavior and document it.

## Not Done
Not done if only docs/error copy changed, a draft/open PR remains, CI fails, or P0/P1/P2 findings remain.

## Persistence
Use `RETRO.md` as the ledger and continue through CI/merge unless a stop rule fires.
