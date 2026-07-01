/goal Work in /Users/mg/Developer/outfitter/dispatch. Use packet .agents/goals/2026-07-01-history-capture-policy.

## Objective
Ship the next provider-history slice: capture tiers, standard/default Tier 1+2 capture, debug raw-payload capture, and first DB-backed history/search/status reads.

## Definition Of Done
Completion horizon: ready-pr. New PRs above this packet branch are pushed and ready after checks, docs/skills, review, and RETRO/tracker evidence.

## Not Done
Docs-only work, draft/failing PRs, unpushed commits, unresolved P0/P1/P2, unbounded raw capture, or DB-backed claims that still render only from App Server.

## Authority
You may create Graphite branches, commit, push, open/mark ready PRs, update Dispatch Linear, run bounded subagents, and run isolated temp-state scenarios. Do not merge, release, publish, make Turso/libSQL default, mutate global Codex/Claude config, or use live state as a fixture.

## Boundary
Stay inside dispatch, PR #48/#49, Dispatch Linear, and this packet. Preserve unrelated dirty work. Full Claude ingestion and remote gateway work are out of scope.

## Sequence
Stack: feat/history-capture-policy, feat/history-standard-capture, feat/history-debug-capture, feat/db-backed-history-surfaces. Add docs branch only if clearer. Restack as needed.

1. Capture policy config/types/helpers for minimal|standard|debug, bounded text/payload retention, doctor/status visibility.
2. Standard capture expands Tier 1 operational and Tier 2 searchable facts from Codex live events, thread/read, and sync/backfill without unbounded raw retention.
3. Debug mode retains richer provider payloads/reducer evidence with byte caps, truncation markers, warnings, and tests proving standard stays bounded.
4. Make at least one meaningful history/search/get/list path read normalized DB tables when fresh enough, with honest live-refresh/fallback behavior and CLI/MCP/schema parity.
5. Final docs/skills/PR/Linear hardening and full-stack review.

## Loop
For each branch: inspect current docs/code, implement the smallest coherent slice, add focused tests/fixtures, update docs/skills, run focused checks, commit, local-review with standing+targeted reviewers, fix P0/P1/P2 plus worthwhile P3, rerun reviewers as needed, update RETRO.md and Linear, then continue.

## Hard Rules
Keep CLI/MCP derived from contracts. Standard capture stays bounded. Debug capture must be explicit, capped, and warned. Never test by mutating live user threads/config.

## Verification
Run focused pytest; fixture/replay tests for schema/history changes; ruff/mypy on touched source; CLI/MCP/schema/parity for surface changes; dispatch schema/help smokes; final just check. Optional live scenarios need temp DISPATCH_HOME/CODEX_HOME or read-only safe paths.

## Review Gate
Every milestone needs local-review JSON under .agents/goals/2026-07-01-history-capture-policy/tmp/reviews/. Do not move upward or mark PRs ready with unresolved P0/P1/P2. Final full-stack review uses the standing reviewer plus one fresh reviewer.

## Stop Rules
Stop only for unsafe live-state risk, irreconcilable dirty worktree, external auth/tooling blocking all useful work, or a required product decision about horizon, storage default, raw payload defaults, merge, or release.

## Evidence Contract
RETRO.md must list branch order, commits, PR URLs, Linear state, checks/results, review paths/scores, P0-P2 closure proof, docs/skills changes, capture behavior, DB-backed surface proof, residual risks, and forbidden-action audit.

## Next Move
Verify current stack, create feat/history-capture-policy above this packet branch, and start milestone 1. If a milestone is too large, split a branch and record the amendment.

## Persistence
Use this packet as the resume surface. Update RETRO.md after every milestone, review, branch split, tracker mutation, deferment, and final state.
