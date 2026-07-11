/goal From `/Users/mg/Developer/outfitter/dispatch`, execute `.agents/goals/2026-07-09-app-server-adoption/` through merged, tracker-reconciled, clean-main completion. Do not publish a release.

## Objective
Land as much of the clear App Server 0.144 adoption packet as engineering quality permits: first settle PRs #73/#74, then complete DIS-42, DIS-44, DIS-45, DIS-35/DIS-39, DIS-18, DIS-46, and DIS-47 in that order unless dependencies justify a recorded amendment.

## Authority And Boundary
- Commit, push, create Graphite branches/PRs, mark ready, merge in order, resolve reviews, and update Linear/docs/skills without asking.
- Use bounded subagents for exploration, implementation, fixtures, and review; verify their work. Subagents do no source-control or tracker mutations.
- Do not publish, touch secrets/auth material, destructively probe real threads, implement realtime voice or DIS-43, redeem credits, or broaden into mesh/gateway work.

## Loop Per Milestone
1. Read AGENTS.md, SPEC.md, GOAL.md, REFS.md, the issue, adjacent code/tests/docs, and prior RETRO evidence.
2. Create one coherent Graphite slice, implement contract-first, derive CLI/MCP, add provider-DB handling where relevant, fixtures/tests, docs, help/schema, and first-party skill changes. Preserve stable fallbacks and capability-gate experimental methods.
3. Run `uv run pytest tests/client tests/core tests/registry`, affected CLI/schema/JSON/MCP parity smokes, `just test-int`, and the isolated `just scenario -- tests/scenarios/app_server_adoption.toml` as it becomes applicable, then `just check`.
4. Run `$local-review` targeted mode. Fix/re-review until 5/5 with zero open P0/P1/P2; fix easy worthwhile P3s and record residuals.
5. Update RETRO and Linear, commit/push, submit the PR with a complete description, clear CI/review threads, mark ready, and merge when dependencies allow. Continue to the next slice.

## Required Outcomes
- No App Server server request can silently strand a turn; behavior is explicit, configurable, audited, and safe for unsupported categories.
- Canonical 0.144 items and refs are DB-indexed and replay-safe; Codex parent/descendant lineage is durable but does not imply Dispatch authority.
- `dispatch usage` is redacted, jq-friendly, and equivalent through grouped MCP.
- Resume observes immediately and backfills recent-to-old within durable bounds.
- Permission profiles work with presets; text/image inputs work through derived `new`/`send` surfaces.

## Hard Rules
- Do not hand-wire per-surface behavior or add one MCP tool per protocol method.
- Never persist secrets, auth payloads, raw binary media, or unbounded provider payloads.
- Keep commits/PRs issue-shaped; do not start a dependent milestone before the current review gate is clean.
- A blocked slice does not stop independent work. After three failed approaches, change tactics, record evidence, continue, and return later.

## Final Gate
Run `just check`, relevant `just test-int` and scenario fixtures, generated-manifest drift checks, CLI `--help`/`schema`/`--json` smokes, and an independent full-stack local review. Clear all P0-P2 and GitHub review threads. Merge the stack, `gt sync`, verify clean current `main`, reconcile Linear, and finalize RETRO with commits, PRs, checks, live evidence, review scores, risks, and forbidden-action audit.

Not done means local-only work, unmerged PRs, stale docs/skills/Linear, missing live evidence, open P0-P2, or skipped feasible milestones. Stop only for secrets, destructive authority, an irreversible security/product decision, or repeated external failure after independent work is exhausted. Persist through CI/review waits using RETRO as the resume surface and poll meaningful state about every 10 minutes.
