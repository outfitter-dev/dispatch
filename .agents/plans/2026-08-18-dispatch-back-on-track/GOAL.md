# Goal Prompt: Dispatch Back On Track

Paste this into the goal runtime:

```markdown
/goal Continue the tracked recovery packet from `/Users/mg/.config/codex/worktrees/dispatch-back-on-track-goal` on `codex/dispatch-back-on-track-goal`. Read `AGENTS.md`, `.agents/plans/PLANNING.md`, and this packet's `PLAN.md`, `REFS.md`, and `RETRO.md`; re-read live Git, GitHub, Linear, dependency-graph, runtime, and tool state before acting. Live evidence outranks the packet.

Current checkpoint: PRs #93-#96 are merged in the authorized order and live `main` is `ea4b7313d6397364e5001ac33f7eb4396dfd7e12`. Merged `main` passed locked sync, both CLI smokes, exact `just check` (748 passed, 17 deselected), package validation, and `just test-int` (17/17, no skips). DIS-57's contained Claude 2.1.228 launch reconciled one full UUID and cleaned the isolated roster to zero. Herdr 0.8.0 and cmux 0.64.22 are installed; DIS-62 and DIS-63 are Done with optional-for-observation/reject-for-automated-input decisions; DIS-61 is Todo. Every owned worktree is clean and no synthetic evaluation process/session/temp path remains.

Do not redo merged work, live provider exercises, host evaluations, or broad verification. The sole completion gate is DIS-66: GitHub's current dependency-graph SBOM already reports cryptography 50.0.0, mcp 1.28.1, pydantic-settings 2.14.2, python-multipart 0.0.31, and starlette 1.3.1, but Dependabot alerts #1-#9 still report OPEN with stale timestamps. Keep DIS-66 In Review; never dismiss the alerts or push a no-op commit.

Continue bounded polling. After one hour from the successful default-branch graph run (2026-08-18T17:37:00Z), if all nine records remain stale/open, use GitHub's documented **Refresh Dependabot alerts** action once, then wait for the refreshed graph/alert evaluation. Completion requires all nine records to become fixed/closed with `fixed_at` set. If the supported refresh completes and the records still remain open, stop and document a GitHub Support escalation packet with PR #96, dependency-graph run 32166480161, the current SBOM creation time, and stale alert timestamps; do not weaken the acceptance gate.

When the API reports zero open alerts: add the final evidence comment to DIS-66, mark it Done, update `RETRO.md`, run the final clean-state audit, archive the packet per `.agents/plans/PLANNING.md`, commit/push the coordination branch, and mark the goal complete. Report exact verification, remaining backlog order, and cleanup. Use markdown links for every PR and Linear/GitHub issue reference.

Hard rules: preserve Studio history, worktrees, notes, ignored files, and unrelated state. Never expose secrets, settings contents, prompt/customer content, or provider transcripts. No package publish, new PR/readiness/merge, credential mutation, public provider enablement, or unsafe host input. Subagents remain read-only reviewers; the main agent owns source-control and tracker writes. Update `RETRO.md` before any pause, refresh, tracker completion, or archive.
```
