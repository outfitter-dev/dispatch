# CLI/MCP Ergonomics — quality loop goal

Pasteable `/goal` for a deceptively simple quality loop. The executor should
read the queued issues in [`RETRO.md`](./RETRO.md), then loop until the branch
is actually good.

```text
/goal From `/path/to/dispatch`, run the CLI/MCP ergonomics quality loop on branch `feat/cli-mcp-ergonomics`.

Read first: `AGENTS.md`, `.agents/plans/cli-mcp-ergonomics/RETRO.md`, and the touched CLI/MCP/handler tests.

Start with the queued quality findings in `RETRO.md`. Then repeat up to 5 loops:
1. Find the highest-value bug, debt, missing test, stale doc, or less-than-good code in this branch.
2. Fix one coherent slice.
3. Run focused tests, then `just check` when the slice could affect the gate.
4. Commit the slice with a conventional commit message.
5. Review the branch locally; fix P0/P1/P2 before the next loop.
6. Update `RETRO.md` with what changed, checks, review result, and remaining risk.

Keep the loop boring and sharp. Use subagents only for bounded review or scouting, and verify their claims yourself. Do not open a PR, push, merge, publish, or touch live user agent state.

Stop when either: queued findings are closed, `just check` is green, local review has no P0/P1/P2, `RETRO.md` is accurate, and you would be comfortable shipping the branch; or 5 loops are complete; or the same blocker repeats 3 times.

Final report: loops run, commits made, exact checks, review result, queued issues closed/deferred, remaining risks, and final git state.
```
