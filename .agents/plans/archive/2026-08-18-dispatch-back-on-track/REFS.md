# References: Dispatch Back On Track

## Tracked / Portable Sources

- `AGENTS.md` - project contract, commands, source-control and review rules.
- `.agents/plans/PLANNING.md` - goal packet, review, approval, and archive rules.
- `docs/development/design.md` - approved architecture and vocabulary.
- `docs/research/app-server-verification.md` - compatibility and runtime evidence.
- `.agents/plans/v0/PLAN.md` and `.agents/plans/v0/RETRO.md` - historical v0
  implementation context; not current execution authority.
- `src/outfitter/dispatch/core/claude_launch.py` and
  `tests/core/test_claude_launch.py` - internal Claude launch seam and proof.

## Untracked / Local-Only Sources

- `.agents/notes/` - 10 preserved Studio/local continuation notes; not copied into
  this packet and not a cleanup target.
- `.agents/goals/*/tmp/` - machine-local runtime artifacts; excluded.
- `.claude/settings.local.json`, `.claude/worktrees/`, `.venv/`, and `dist/` -
  machine-bound, active, or rebuildable state; preserved but not authoritative.
- `/Users/mg/.config/codex/worktrees/dispatch-studio-bridge-20260818` - recovered
  compatibility worktree, now clean and represented by PR #93.
- `/Users/mg/.config/codex/worktrees/dispatch-dis-64` - clean PR #94 stack-tip
  worktree; preserve its ownership and branch.
- `/Users/mg/.config/codex/worktrees/dispatch-dis-57` - clean PR #95 worktree at
  `f51e95e`; the existing Sol Low implementer owned only its scoped review fixes.
- `/Users/mg/.config/codex/worktrees/dispatch-dis-66` - clean lock-only security
  worktree for [DIS-66](https://linear.app/outfitter/issue/DIS-66).

## Tracker Records

- [DIS-65](https://linear.app/outfitter/issue/DIS-65) - Codex 0.147 evidence refresh.
- [DIS-64](https://linear.app/outfitter/issue/DIS-64) - minimum supported Codex floor.
- [DIS-57](https://linear.app/outfitter/issue/DIS-57) - supported Claude background launch.
- [DIS-62](https://linear.app/outfitter/issue/DIS-62) and
  [DIS-63](https://linear.app/outfitter/issue/DIS-63) - completed Herdr/cmux
  evaluations with optional-observation/reject-automated-input guidance.
- [DIS-61](https://linear.app/outfitter/issue/DIS-61) - host abstraction decision,
  now Todo after the two evaluation blockers completed.
- [DIS-58](https://linear.app/outfitter/issue/DIS-58) ->
  [DIS-59](https://linear.app/outfitter/issue/DIS-59) ->
  [DIS-54](https://linear.app/outfitter/issue/DIS-54) - downstream Claude control chain.
- [DIS-50](https://linear.app/outfitter/issue/DIS-50) - vertical provider slice,
  blocked by DIS-57, DIS-61, and DIS-54.
- [DIS-66](https://linear.app/outfitter/issue/DIS-66) - High-priority remediation
  remains In Review while [GitHub Support case #4677807](https://support.github.com/ticket/personal/0/4677807)
  investigates stale reconciliation for all nine runtime dependency alerts.
- [DIS-67](https://linear.app/outfitter/issue/DIS-67) - deferred metadata-only
  v0.11.0 candidate in Backlog. The clean branch
  `dis-67-prepare-the-v0110-release-candidate` is preserved at `f0a475a`; it was
  pushed before the scope narrowed, but no PR, tag, release, or publication exists.
- [#16](https://github.com/outfitter-dev/dispatch/issues/16),
  [#17](https://github.com/outfitter-dev/dispatch/issues/17),
  [#25](https://github.com/outfitter-dev/dispatch/issues/25), and
  [#26](https://github.com/outfitter-dev/dispatch/issues/26) - open community backlog
  with evidence-based scope comments.

## PRs / Branches

- [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) -
  `codex/recover-studio-bridge-20260818`, merged as `e63b5388d597219a034fa7da618944f7fc3b0d5a`.
- [PR #94](https://github.com/outfitter-dev/dispatch/pull/94) -
  `dis-64-enforce-a-minimum-supported-codex-build-instead-of-an-unenforced-exact`,
  stacked on [PR #93](https://github.com/outfitter-dev/dispatch/pull/93), restacked
  by Graphite and merged as `260f77c8fa15d9e6e6fb5ec396399cb2af363f69`.
- [PR #95](https://github.com/outfitter-dev/dispatch/pull/95) -
  `dis-57-launch-claude-background-sessions-through-the-supported-cli`, independent
  from the compatibility stack; merged as `1b6fc3ffadf40ff72386297bf9d288e31fbe2bf6`.
- [PR #96](https://github.com/outfitter-dev/dispatch/pull/96) - independent
  lock-only [DIS-66](https://linear.app/outfitter/issue/DIS-66) slice, merged as
  `ea4b7313d6397364e5001ac33f7eb4396dfd7e12`; alert reconciliation pending.
- `codex/dispatch-back-on-track-goal` - this coordination packet.

## Validation Commands

- `uv sync` - materialize the locked development environment.
- `uv run pytest <focused paths>` - narrow behavior proof.
- `just check` - Ruff, format, strict mypy, full pytest, build, package content.
- `uv build` - independently produce sdist and wheel.
- `uv run dispatch --help`; `uv run dispatchd --help` - installed entry-point smoke.
- `gh pr checks <number>` plus review-thread query - live hosted gate state.
- `just test-int` (`uv run pytest -m integration`) - real App Server proof through
  the harness's temporary `CODEX_HOME` and ephemeral lanes; auth/tool-dependent
  skips must be recorded. `just scenario` remains excluded absent separate approval.
- `claude agents --json --all`, `claude stop`, and `claude rm` - contained
  background-session identity and cleanup proof.
- `herdr` 0.8.0 and `cmux` 0.64.22 - installed evaluation binaries; both
  decisions and cleanup evidence are recorded in `RETRO.md` and Linear.

## Current Verification Baseline

- [PR #93](https://github.com/outfitter-dev/dispatch/pull/93): `just check`,
  697 passed / 17 deselected; local review 5/5.
- [PR #94](https://github.com/outfitter-dev/dispatch/pull/94): `just check`,
  715 passed / 17 deselected; combined compatibility review
  5/5; hosted CI green and zero unresolved threads at the recorded head;
  `just test-int` passed 17/17 real isolated App Server tests in 231.65s.
- [PR #95](https://github.com/outfitter-dev/dispatch/pull/95): `just check`,
  729 passed / 17 deselected at `f51e95e`; hosted CI,
  CodeQL, and Graphite are green with zero unresolved review threads; fresh local
  round 3 scored 5/5 with no findings. Two earlier 3/5 rounds found post-start
  indeterminate-outcome gaps, both fixed with focused regression tests.
- [PR #96](https://github.com/outfitter-dev/dispatch/pull/96): locked sync and CLI
  smokes pass; 20 focused MCP tests; exact `just check` with 696 passed / 17
  deselected; `just test-int` passed 17/17 in 236.94s; independent lock review
  5/5; CI/CodeQL green; merged at `ea4b731`.
- Merged `main` at `ea4b731`: exact `just check` passed with 748 tests and 17
  deselected; `just test-int` passed 17/17 with no skips; both CLI smokes and
  package validation passed.
- GitHub dependency-graph SBOM reports every patched package version, while the
  nine Dependabot alert records remain stale/open; [DIS-66](https://linear.app/outfitter/issue/DIS-66)
  intentionally remains In Review.

## GitHub Support Escalation Packet

Subject: Dependabot alerts remain open after fixed default-branch graph rebuild

Submitted as [GitHub Support case #4677807](https://support.github.com/ticket/personal/0/4677807)
on 2026-08-18; status Open.

- Repository: <https://github.com/outfitter-dev/dispatch>
- Remediation: [PR #96](https://github.com/outfitter-dev/dispatch/pull/96),
  merged on `main` as `ea4b7313d6397364e5001ac33f7eb4396dfd7e12`.
- Automatic dependency graph: [run 32166480161](https://github.com/outfitter-dev/dispatch/actions/runs/32166480161)
  succeeded for the merged commit at 2026-08-18T17:37:00Z.
- Current SBOM proof (queried 2026-08-18T18:54:40Z): only `cryptography 50.0.0`,
  `mcp 1.28.1`, `pydantic-settings 2.14.2`, `python-multipart 0.0.31`, and
  `starlette 1.3.1`; all meet every alert's first patched version.
- Supported retry: after more than one hour, **Refresh Dependabot alerts** was
  invoked exactly once. GitHub advanced the alert-page graph build to the
  current `ea4b731` commit, proving the rebuild processed current default-branch
  dependency files.
- Defect: ten minutes after that rebuild, Dependabot alerts 1-9 still returned
  `state: open`, `fixed_at: null`, and their original June-August `updated_at`
  timestamps. No alert was dismissed and no security setting changed.
- Expected: the nine alerts close as fixed against the patched graph.
- Tracker: [DIS-66](https://linear.app/outfitter/issue/DIS-66) remains In Review
  and contains the same evidence.
