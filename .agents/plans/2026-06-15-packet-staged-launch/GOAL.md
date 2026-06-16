# Packet-staged launch - pasteable goal

````text
/goal Work in /Users/mg/Developer/outfitter/dispatch. Implement the packet-staged launch plan in .agents/plans/2026-06-15-packet-staged-launch/PLAN.md. Use repo AGENTS.md and .agents/plans/PLANNING.md as authority.

Objective: make `dispatch new` support durable launch packets and staged session files for parallel worker lanes, without making Dispatch own any domain workflow. Add `--input-file`, `--goal-file`, `--packet`, `--dry-run --json`, and staged session support under `.agents/sessions/<dispatch-ref>/`; preserve native App Server goals; support output schemas when packet/config provides them; investigate native Codex worktree and hook/config behavior before implementing any assumptions.

Key product constraints: prefer `--input-file` over `--text-file`; support stdin (`--goal-file -`, `--input-file -`) but reject multiple stdin consumers; `goal.md` is a native goal, not `/goal` text; packet format starts with `dispatch.toml`, `goal.md`, `prompt.md`, optional `output.schema.json`, `base.md`, `developer.md`, `hooks/`, and `codex/`; `--stage` with no value means all stageable packet parts and later accepts parts; `--inline` is the dual vocabulary; staged files land in the actual launched cwd at `.agents/sessions/<ref>/packet/` with `scratch/` and `state.json`; Dispatch may stage hook/config files but must not execute arbitrary hooks; hook trust bypass requires explicit operator/trusted-config authority, not packet-only authority; do not guess Codex worktree paths.

Plan loop: first read PLAN.md, REFS.md, current `dispatch schema new`, relevant code in `src/outfitter/dispatch/core/models.py`, `core/handlers.py`, `core/new_config.py`, `client/models.py`, `contracts/derive_cli.py`, and existing tests. Work in small Graphite-style phases; update RETRO.md before each handoff. After each meaningful phase, run focused tests. Before broad handoff run `just check`. Request local review using repo P0-P3/score contract; fix P0/P1/P2; record P3s fixed/deferred.

Validation minimum: focused unit/projection tests for packet resolution, stdin exclusivity, dry-run no mutation, output schema parsing, stage path/ref behavior, staging failure before turn start, JSON output shape; docs and schema/help updates; `just check` green. Any live App Server worktree/hook probe must use isolated DISPATCH_HOME/CODEX_HOME and never the user’s live daemon/state.

Stop/pause rules: stop if current App Server schema has no native worktree field; stop before exposing hook trust bypass without explicit trusted authority; stop if hook/config reload semantics cannot be proven; stop if implementation requires unrelated surface rewrites; report exact blocker and update RETRO.md. Do not merge, publish, or submit non-draft PRs without explicit user approval. Completion requires final RETRO.md with commands/results, review status, source-control state, forbidden-action audit, remaining risks, and transcript-visible proof.
````

