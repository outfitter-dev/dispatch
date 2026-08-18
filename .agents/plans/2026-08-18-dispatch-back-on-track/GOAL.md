# Goal Prompt: Dispatch Back On Track

Paste this into the goal runtime:

```markdown
/goal From `/Users/mg/.config/codex/worktrees/dispatch-back-on-track-goal` on tracked branch `codex/dispatch-back-on-track-goal`, execute `.agents/plans/2026-08-18-dispatch-back-on-track/PLAN.md` end to end; use its `RETRO.md` as the durable ledger. Preflight that all four packet files are tracked before acting. Use `/Users/mg/Developer/outfitter/dispatch` as the primary `main` checkout and the exact isolated worktrees listed in `REFS.md` for branch-specific work.

Read first: `AGENTS.md`, `.agents/plans/PLANNING.md`, the packet's `PLAN.md` and `REFS.md`, `docs/development/design.md`, `docs/research/app-server-verification.md`, and every linked PR/Linear issue. Re-read live Git, GitHub, Linear, tool, and runtime state before acting; live evidence outranks the packet.

Objective: restore Dispatch to a clean, current, sustainably continuable state by closing review debt on PRs #93-#95, landing them in the correct order after explicit approval, proving the merged repo locally and through isolated App Server integration, and leaving the Claude host path dependency-ordered with contained live-test gates.

Work loop: execute checkpoint by checkpoint. After each turn report checkpoint, changes, exact checks/artifact proof, result, remaining work, blockers, and next checkpoint. If stuck, shrink the failing surface before retrying. Update `RETRO.md` after every meaningful implementation, review, CI, tracker, PR, merge, packaging, or authorization change and immediately before handoff/pause.

Validation ladder: run focused tests after each change; before merge readiness run `git diff --check`, targeted tests, and exact `just check` on every final PR tip. After landing, safely fast-forward local `main`, run `uv sync`, `just check`, `uv build`, `uv run python scripts/check_package_contents.py`, both CLI help smokes, and `just test-int`. The integration harness must use its temporary `CODEX_HOME` and ephemeral lanes; unavailable/auth-dependent skips are acceptable only when recorded with the exact proof needed to run them. Do not substitute opt-in `just scenario` model workflows without separate authorization. Live Claude proof is a separate approval gate.

Review loop: require scored local review with score n/5, summary, P0-P3 findings, and prompt-to-fix text. Fix and re-review every P0/P1/P2. Hosted CI must be green and all actionable review threads must be replied to and resolved. Keep PR #94 stacked on PR #93; PR #95 is independent.

Hard rules: preserve Studio history, worktrees, notes, ignored files, and unrelated changes. Never expose secrets or prompt/customer content. PRs #93-#95 were already non-draft before this packet; do not create or change readiness, merge, publish packages, mutate production/personal provider state, or run live Claude without the explicit approval required by repository policy. Never use a personal Claude workspace for evaluation. No public provider abstraction or Agent View implementation before its blockers. Subagents are read-only reviewers except the existing Sol Low owner explicitly delegated the P1 fix in the isolated DIS-57 worktree; the main agent owns all other source-control writes.

Done only when PRs #93-#95 have no open P0/P1/P2 and are landed after approval; clean local `main` equals live `origin/main`; all non-live checks pass; DIS-57 live proof is either completed under authorization or explicitly gated without false completion; GitHub/Linear/dependencies agree; constraints hold; and `RETRO.md` has final tracker, PR, review, verification, forbidden-action, risk, and archive-readiness state.

Stop/ask if plan/repo/tracker truth diverges, public scope changes, merge/publication/provider mutation is needed, isolation cannot be proven, required access is absent, unrelated verification fails after a focused retry, or a provider side effect becomes indeterminate.
```
