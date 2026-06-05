# Stable refs and flat CLI - pasteable goal

```text
/goal Work in /Users/mg/Developer/outfitter/dispatch. Implement the stable refs and flat CLI/MCP reshape described in .agents/plans/stable-refs-flat-cli/PLAN.md.

First verify live repo state, PR #32/top-level thread actions state, and current Graphite stack. If PR #32 is not merged, either stack on it or stop with the exact reason if stacking would be unsafe. Do not rely on chat memory when the plan packet or repo state disagrees.

Objective: ship dispatch-local short refs for managed lanes, a shared selector resolver, a flatter thread-oriented CLI, a split between persisted `tail` and bounded live `watch`, and updated MCP/docs/skills/ADRs. Keep the full Codex UUID accepted everywhere. Treat titles and @handles as mutable convenience labels, not stable identity. Keep `dispatch mcp` as the top-level MCP server entrypoint.

Implementation constraints:
- Preserve contract-first/no-drift architecture. Add behavior as ops/models/derived routes, not separate hand-written surfaces.
- Every managed lane must have a unique stored `ref`.
- Ref format is `<source><payload4><mixer>`, with source `0` for Codex, payload from `sha256("codex:" + thread_id)` encoded in base58btc, and mixer allocated by the registry on collision.
- Mutating/destructive commands must not fuzzy-resolve ambiguous names.
- `tail` means persisted conversation history. `watch` means bounded live App Server event sample. Do not present `tail --follow` as canonical.
- Flatten canonical CLI commands away from `dispatch lane ...`; no compatibility aliases are required unless they are temporary and clearly non-canonical.
- `dispatch new --no-send` is the open-without-initial-turn shape; do not keep a separate canonical `open`.
- `dispatch list` is the managed-thread overview and `dispatch list --unmanaged` is discover. `dispatch search` needs global, thread-focused, repo/dir, managed/unmanaged, and date-window filters where supported.
- `rename`, `archive`, and `restore` should accept refs and full Codex thread ids. `restore` must not start a turn.
- MCP should stay grouped by workflow/safety and must keep exact annotations; do not mirror every CLI command into its own tool.
- Update README, docs/usage, design docs, ADRs, root AGENTS/CLAUDE guidance, `.claude/rules`, skills, plugin/MCP docs if affected, schema examples, and tests.

Work loop:
1. Read PLAN.md and REFS.md.
2. Inspect current code and route/schema shape.
3. Implement in small coherent slices.
4. Add/update tests for allocator, migration, resolver, CLI routes/schema, MCP projection, output schemas, and docs/skill drift.
5. Run focused tests first, then `just check`.
6. Run a local review loop; fix all P0/P1/P2 findings.
7. Submit as one or more draft PRs only after local checks are green.
8. Keep RETRO.md current with decisions, checks, review results, PRs, and any deferred P3s.

Done only when the branch or stack is draft-submitted, green locally, review P0/P1/P2 clear, docs/skills/ADRs updated, and RETRO.md contains the final proof.
```
