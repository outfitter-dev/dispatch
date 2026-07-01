/goal Work in /Users/mg/Developer/outfitter/dispatch. Use goal packet .agents/goals/2026-07-01-provider-event-log-history-index.

## Objective
Execute ADR-0023 and Linear project Provider Event Log and History Index (DIS-1 through DIS-10) as a Codex-first provider event/history substrate.

## Definition Of Done
Completion horizon: ready-pr. Code, docs, tests, fixtures, and skills for a coherent Codex-first substrate are committed, pushed, opened as PRs, and ready after local checks and review.

## Not Done
Only ADR/tracker setup, a DB schema no Codex path dogfoods, green checks with unresolved P0/P1/P2, stale docs/skills, or unpushed local work.

## Authority
You may create branches, commit, push, open draft/ready PRs, update Linear issues/comments, and use bounded subagents for exploration/review.

## Boundary
Stay inside the dispatch repo and the Dispatch Linear project unless explicitly approved. Preserve unrelated dirty work. Do not mutate user global config, touch live ~/.codex, make Turso the default store, merge, publish, or release.

## Sequence
DIS-1/DIS-7 schema plus replay fixtures; DIS-2/DIS-3 Codex live event persistence plus reducers; DIS-4/DIS-5 backfill plus progressive JSONL sync; DIS-6/DIS-10 DB-backed surfaces, docs, and skills; DIS-8/DIS-9 Turso/libSQL and Claude hook boundary spikes or follow-ups.

## Loop
For each slice: inspect docs/code, implement the smallest coherent change, add focused tests/fixtures, update docs/skills, run focused checks, commit, review, fix P0-P2 and worthwhile P3, update RETRO.md, update Linear, then continue.

## Hard Rules
Keep CLI/MCP surfaces derived from contracts. Use isolated fixtures/fakes. Live app-server scenarios are opt-in and must not use real user state.

## Verification
Run focused pytest for touched modules, replay fixture tests, CLI/MCP schema and parity tests for surface changes, uv run dispatch --help or relevant command schemas when help changes, and just check before PR readiness.

## Review Gate
Run local review after meaningful slices and once across the full stack. Fix all P0/P1/P2 findings before moving on or marking ready.

## Stop Rules
Stop only for unsafe live-state risk, missing external auth/tooling that blocks all useful work, irreconcilable dirty-worktree conflict, or a required product decision about horizon, merge, release, or storage default.

## Evidence Contract
RETRO.md must summarize issues handled, commits/PRs, exact checks, review results, Linear state, docs/skills changes, remaining risks, forbidden-action audit, and proof the result is ready-pr.

## Next Move
Begin with DIS-1/DIS-7. If a check fails, narrow, fix root cause, rerun focused checks, then broaden. If scope is too large, reduce to a coherent slice and record deferments.

## Persistence
Use this packet as the resume surface. Update RETRO.md after every slice, review, meaningful deferment, tracker mutation, and final state.
