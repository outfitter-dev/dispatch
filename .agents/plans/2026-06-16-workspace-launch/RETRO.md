# Workspace launch - execution ledger

Durable execution ledger for workspace launch support.

## Execution Summary

- 2026-06-16: Planning packet created from Matt/Codex discussion about
  worktree-backed Dispatch lanes, Codex environment files, and `--workspace auto`.
- 2026-06-16: Implemented the first workspace preflight slice in the existing
  working tree: `--workspace none|auto|<preset>`, environment discovery, dry-run
  reporting, explicit/trusted setup execution, docs, and focused tests.
- No branches, commits, PRs, merges, publishes, or tracker updates.

## Starting Context

Captured by `context-prime.sh` on 2026-06-16 11:59 EDT:

- cwd: `/Users/mg/Developer/outfitter/dispatch`
- branch: `main`
- state: `main...origin/main`
- current head: `de56907 Merge attached-lane write policy`
- open PRs for current branch: none
- working tree had uncommitted Dispatch changes for packet-adjacent follow-up
  work: runtime settings persistence, stale busy fix, `history`, docs/skill
  worktree guidance, and related tests.

Executor note: verify current repo state before editing. Do not assume the
working tree is clean or that these changes have landed.

## Branch / PR / Issue Ledger

- Planning packet only (planner).
- Execution branch: not created by planner.
- PR: none.
- Issues: none created or updated.

## Tracker Mutations

- None.

## Decisions Captured

- Prefer `--workspace auto|none|<preset>` over exposing `--worktree` as if it
  were native App Server behavior.
- First-pass environment metadata target:
  `.codex/environments/environment.toml`.
- Supported environment v1 fields: `version`, `name`, `[setup].script`,
  `[cleanup].script`.
- Discovery/reporting can be automatic; setup execution requires explicit
  trusted authority.
- Packet-local config must not grant setup execution trust by itself.
- Effective cwd and stage path must be exact and reported.
- Git worktree creation is a later phase only if repo environment setup does not
  cover the need.

## Execution Log

### Planning

- Read `/Users/mg/.agents/skills/goal-planning/SKILL.md`.
- Read `.agents/plans/PLANNING.md`.
- Ran `/Users/mg/.agents/skills/goal-planning/scripts/context-prime.sh`.
- Inspected prior packet-staged launch packet for house style and protocol
  findings.
- Inspected current `NewInput`, packet, launch, staging, docs, and tests.
- Checked Athena and Trails `.codex/environments/environment.toml` examples.

### Implementation

- Added runtime policy fields for workspace setup trust and timeout.
- Added repo launch config support for `[workspace] default` and
  `[workspace.presets.<name>] mode`.
- Added `core/workspace.py` for `.codex/environments/environment.toml`
  discovery, environment TOML parsing, setup policy decisions, and bounded setup
  execution.
- Wired workspace preflight into `new-plan` and `new` so dry-run and launch JSON
  include workspace facts, and thread/start, registry cwd, staging, and initial
  turn use the effective cwd.
- Added focused tests for no-op, auto discovery, invalid TOML, named presets,
  dry-run no setup, explicit setup, setup failure before thread creation, and
  CLI projection.
- Updated operator docs and the Dispatch skill.
- Local self-review found and fixed a runtime policy edge where setting
  `DISPATCH_ALLOW_ATTACHED_WRITES` would have bypassed the config file entirely
  and dropped workspace setup policy. `runtime_policy()` now reads config first
  and applies the env override only to attached writes.
- Added explicit Dispatch-owned git worktree creation via `--worktree create`,
  `--worktree-path`, `--worktree-branch`, and `--worktree-base`. The default root
  is `~/.dispatch/worktrees/<repo>/<lane>/` (or `DISPATCH_WORKTREE_ROOT`), not
  repo-local `.dispatch/worktrees/` and not Claude/Codex private path schemes.
- Added workspace config support for worktree defaults in repo
  `.dispatch/config.toml`: `[workspace] worktree/worktree_path/worktree_branch/
  worktree_base` and matching `[workspace.presets.<name>]` overrides. Explicit
  CLI worktree flags still win.

## Verification Log

- `context-prime.sh` completed.
- Plan packet written under `.agents/plans/2026-06-16-workspace-launch/`.
- Focused tests:
  `uv run pytest tests/core/test_workspace.py tests/core/test_new_config.py tests/core/test_handlers.py::test_plan_new_lane_reports_workspace_without_setup tests/core/test_handlers.py::test_new_lane_workspace_auto_uses_effective_repo_cwd tests/core/test_handlers.py::test_new_lane_workspace_setup_requires_policy_or_explicit_run tests/core/test_handlers.py::test_new_lane_workspace_setup_runs_with_explicit_run tests/core/test_handlers.py::test_new_lane_workspace_setup_failure_prevents_thread_start tests/surfaces/test_derive_cli.py::test_new_stage_and_inline_pass_through tests/surfaces/test_derive_cli.py::test_new_json_output_includes_staged_summary -q`
  → passed, 19 passed.
- Broader focused suite:
  `uv run pytest tests/core/test_examples.py tests/core/test_workspace.py tests/core/test_handlers.py tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py tests/test_config.py tests/core/test_new_config.py -q`
  → passed, 132 passed.
- Full gate:
  `just check` → passed: ruff check, ruff format --check, mypy, pytest
  (306 passed, 9 deselected), build, and package contents check.
- Full gate after review fix:
  `just check` → passed: ruff check, ruff format --check, mypy, pytest
  (306 passed, 9 deselected), build, and package contents check.
- Worktree-focused tests:
  `uv run pytest tests/core/test_workspace.py tests/core/test_handlers.py::test_new_lane_dispatch_created_worktree_is_effective_cwd_for_stage_and_thread tests/surfaces/test_derive_cli.py::test_new_stage_and_inline_pass_through tests/core/test_examples.py -q`
  → passed, 14 passed.
- Full gate after Dispatch-owned worktree creation:
  `just check` → passed: ruff check, ruff format --check, mypy, pytest
  (310 passed, 9 deselected), build, and package contents check.
- Focused workspace-config extension checks:
  `uv run pytest tests/core/test_workspace.py tests/core/test_new_config.py tests/surfaces/test_derive_cli.py::test_new_stage_and_inline_pass_through -q`
  → passed, 19 passed.
- Focused typecheck:
  `uv run mypy --strict src/outfitter/dispatch/core/workspace.py src/outfitter/dispatch/core/new_config.py src/outfitter/dispatch/core/models.py`
  → passed.
- `git diff --check` → passed.
- Full gate after workspace worktree config support:
  `just check` → passed: ruff check, ruff format --check, mypy, pytest
  (313 passed, 9 deselected), build, and package contents check.
- Version bump:
  `pyproject.toml` and `uv.lock` updated from `0.6.1` to `0.7.0`.
- Full gate after `0.7.0` version bump:
  `just check` → passed: ruff check, ruff format --check, mypy, pytest
  (313 passed, 9 deselected), build, and package contents check. Built
  `dist/outfitter_dispatch-0.7.0.tar.gz` and
  `dist/outfitter_dispatch-0.7.0-py3-none-any.whl`.

## Local Review Log

- Local self-review, 2026-06-16:
  - Overall score: 4/5.
  - Summary: Workspace slice is scoped and tested; no live daemon smoke was run.
  - Finding fixed: P2 - `src/outfitter/dispatch/config.py` - attached-write env
    override bypassed the rest of the runtime policy file, which would make
    workspace setup policy unexpectedly disappear when the env var is present.
  - Prompt To Fix With AI: Read config first, then apply
    `DISPATCH_ALLOW_ATTACHED_WRITES` only to `allow_attached_writes`; preserve
    workspace setup fields and add a regression test.
  - Residual risk: setup scripts execute through the shell when explicitly
    trusted/requested; docs and policy make that boundary visible, but no live
    Athena/Trails smoke was run.
- Local direction correction, 2026-06-16:
  - Matt clarified that Dispatch-created worktrees should not live under
    repo-local `.dispatch/worktrees/`.
  - Local path inspection showed multiple Claude/Codex private worktree layouts,
    including short-id and generated-name schemes that should not be treated as
    a stable public contract.
  - Implementation adjusted to default to `~/.dispatch/worktrees/<repo>/<lane>/`
    with `DISPATCH_WORKTREE_ROOT` and `--worktree-path` overrides.
  - Follow-up clarified that Dispatch now respects its own repo-local workspace
    definition for worktree defaults, but does not parse undocumented Codex
    private worktree configuration.

## Remote Review / CI Log

- No remote review requested.
- No CI run.

## Forbidden Actions Audit

- No implementation.
- No branch creation.
- No commit.
- No PR.
- No merge.
- No publish.
- No tracker mutation.
- No live Dispatch/Codex daemon mutation.

## Final State

- Status: workspace + Dispatch-owned git worktree implementation complete in the
  working tree; package version bumped to `0.7.0`; not committed.
- Remaining risk: no live App Server smoke was run. The implementation is
  covered by unit/projection tests and does not depend on native worktree
  protocol support.
- Next suggested follow-up: live smoke `dispatch new --workspace auto --worktree
  create --stage all` against a disposable repo or Athena smoke packet with
  isolated `DISPATCH_HOME`/`CODEX_HOME`.
