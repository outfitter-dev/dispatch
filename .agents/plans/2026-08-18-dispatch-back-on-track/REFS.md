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
  [DIS-63](https://linear.app/outfitter/issue/DIS-63) - Herdr/cmux evaluations.
- [DIS-61](https://linear.app/outfitter/issue/DIS-61) - host abstraction decision,
  blocked by the two evaluations.
- [DIS-58](https://linear.app/outfitter/issue/DIS-58) ->
  [DIS-59](https://linear.app/outfitter/issue/DIS-59) ->
  [DIS-54](https://linear.app/outfitter/issue/DIS-54) - downstream Claude control chain.
- [DIS-50](https://linear.app/outfitter/issue/DIS-50) - vertical provider slice,
  blocked by DIS-57, DIS-61, and DIS-54.
- [DIS-66](https://linear.app/outfitter/issue/DIS-66) - High-priority remediation
  for all nine open runtime dependency alerts; In Review.
- [#16](https://github.com/outfitter-dev/dispatch/issues/16),
  [#17](https://github.com/outfitter-dev/dispatch/issues/17),
  [#25](https://github.com/outfitter-dev/dispatch/issues/25), and
  [#26](https://github.com/outfitter-dev/dispatch/issues/26) - open community backlog
  with evidence-based scope comments.

## PRs / Branches

- [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) -
  `codex/recover-studio-bridge-20260818`, head `1db00be452b0512f51d97bc20ac86b67f847c996`.
- [PR #94](https://github.com/outfitter-dev/dispatch/pull/94) -
  `dis-64-enforce-a-minimum-supported-codex-build-instead-of-an-unenforced-exact`,
  stacked on [PR #93](https://github.com/outfitter-dev/dispatch/pull/93), reviewed
  head `142c58a150c444abf4bac1783b639809949c1fb6`.
- [PR #95](https://github.com/outfitter-dev/dispatch/pull/95) -
  `dis-57-launch-claude-background-sessions-through-the-supported-cli`, independent
  from the compatibility stack; current reviewed-fix candidate
  `f51e95e0e16bdb82316270c7625701ef4a98d48f`.
- [PR #96](https://github.com/outfitter-dev/dispatch/pull/96) - draft, independent
  lock-only [DIS-66](https://linear.app/outfitter/issue/DIS-66) slice at
  `8659ee28a895c1796875268ceed9527d855beaa5`; hosted CI/CodeQL green.
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

## Current Verification Baseline

- [PR #93](https://github.com/outfitter-dev/dispatch/pull/93): `just check`,
  697 passed / 17 deselected; local review 5/5.
- [PR #94](https://github.com/outfitter-dev/dispatch/pull/94): `just check`,
  715 passed / 17 deselected; combined compatibility review
  5/5; hosted CI green and zero unresolved threads at the recorded head.
- [PR #95](https://github.com/outfitter-dev/dispatch/pull/95): `just check`,
  729 passed / 17 deselected at `f51e95e`; hosted CI,
  CodeQL, and Graphite are green with zero unresolved review threads; fresh local
  round 3 scored 5/5 with no findings. Two earlier 3/5 rounds found post-start
  indeterminate-outcome gaps, both fixed with focused regression tests.
- [PR #96](https://github.com/outfitter-dev/dispatch/pull/96): locked sync and CLI
  smokes pass; 20 focused MCP tests; exact `just check` with 696 passed / 17
  deselected; independent lock review 5/5; CI/CodeQL green; draft approval gated.
