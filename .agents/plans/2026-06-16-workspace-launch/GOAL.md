# Workspace launch - pasteable goal

````text
/goal Work in /Users/mg/Developer/outfitter/dispatch. Implement the workspace launch plan in .agents/plans/2026-06-16-workspace-launch/PLAN.md; use WORKSPACE.md for design notes and REFS.md for evidence. Follow AGENTS.md and .agents/plans/PLANNING.md.

Objective: add a first-class `dispatch new --workspace` preflight layer so worktree-backed lanes can discover repo-local Codex environment metadata, resolve/report the exact effective cwd, and optionally run trusted setup before thread/start. Do not add fake native `--worktree`: prior schema evidence found no App Server worktree request. Repo-local tooling owns environment/worktree semantics; Dispatch owns generic discovery, policy, setup execution when trusted, dry-run JSON, and launch reporting.

Important current truth: this repo may contain uncommitted Dispatch changes for packet/stage, runtime settings, stale-busy fix, history, and docs. Verify current code before editing; preserve unrelated user/agent changes. If these features are absent because the checkout is stale, stop and report instead of guessing.

Desired CLI shape: `dispatch new --workspace none|auto|<preset> ...`; `none` preserves current behavior; `auto` discovers `.codex/environments/environment.toml`; named presets come from trusted Dispatch config. First environment schema: `version`, `name`, `[setup].script`, `[cleanup].script` as used by Athena and Trails. Packet-local config may request workspace behavior but must not grant setup trust by itself.

Implementation loop: Phase 1 discovery/dry-run only; Phase 2 trusted setup before launch if policy is clear; Phase 3 explicit git worktree creation only if still needed; Phase 4 docs/skill/smoke. Update RETRO.md before each handoff. Use small Graphite-style phases. Run focused tests after each slice and `just check` before completion. Request local review using repo score/P0-P3 contract; fix P0/P1/P2 or record explicit user acceptance.

Validation minimum: tests for no metadata no-op, Athena/Trails TOML parsing, invalid TOML before thread creation, dry-run no script execution, `--workspace none` behavior parity, CLI/MCP/schema projection, trusted setup policy, setup failure preventing thread/start, effective cwd/stage path reporting. Any live probe must use isolated DISPATCH_HOME/CODEX_HOME and never the user's live daemon/state.

Stop rules: stop if App Server now has native worktree fields; stop before trusting packet-local setup execution; stop if setup needs long-running teardown/lifecycle ownership; stop if changes require broad unrelated projection rewrites; stop if dirty user changes make edits unsafe. No merge, publish, non-draft PR, or destructive git action without explicit Matt approval. Completion requires final RETRO.md with commands/results, review state, source-control state, forbidden-action audit, risks, and transcript-visible proof.
````

