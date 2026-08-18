# Goal Plan: Dispatch Back On Track

Date: 2026-08-18
Status: In progress

## Objective

Restore Dispatch to a clean, current, sustainably continuable state by landing
the recovered Codex compatibility and Claude launch work through every required
review gate, proving the merged repository locally, and leaving the remaining
Claude host work in an explicit dependency order with safe authorization gates.

## Completion Condition

The goal is complete only when:

- [PR #93](https://github.com/outfitter-dev/dispatch/pull/93), its stacked
  child [PR #94](https://github.com/outfitter-dev/dispatch/pull/94), and
  independent [PR #95](https://github.com/outfitter-dev/dispatch/pull/95) have
  no open P0/P1/P2 review findings and are landed in the correct order after
  explicit merge approval.
- local `main` matches `origin/main` and passes `uv sync`, `just check`, package
  build/content validation, CLI help smoke tests, and the documented isolated
  App Server integration proof.
- [DIS-57](https://linear.app/outfitter/issue/DIS-57) has a contained live proof
  or is explicitly left gated on live-provider authorization, without claiming
  public enablement.
- GitHub and Linear agree about delivered work, remaining blockers, and the
  order of [DIS-62](https://linear.app/outfitter/issue/DIS-62),
  [DIS-63](https://linear.app/outfitter/issue/DIS-63),
  [DIS-61](https://linear.app/outfitter/issue/DIS-61),
  [DIS-58](https://linear.app/outfitter/issue/DIS-58),
  [DIS-59](https://linear.app/outfitter/issue/DIS-59),
  [DIS-54](https://linear.app/outfitter/issue/DIS-54), and
  [DIS-50](https://linear.app/outfitter/issue/DIS-50).
- all constraints below remain true and `RETRO.md` contains the final evidence.

## Non-Goals

- No public Claude provider enablement, provider abstraction, or Agent View
  implementation before the prerequisite issues are complete.
- No production or personal provider mutation, secret handling, prompt logging,
  unsafe zmx hardening, or use of an existing personal Claude workspace.
- No cleanup of preserved Studio history, worktrees, notes, or ignored state.
- No new readiness transition, merge, package publish, or live-provider exercise
  without the explicit approval required by repository policy. PRs #93-#95
  inherited non-draft status before this packet began.

## Source Of Truth

Read first:

1. `AGENTS.md` and `.agents/plans/PLANNING.md`
2. `docs/development/design.md` and `docs/research/app-server-verification.md`
3. `.agents/plans/v0/PLAN.md` and `.agents/plans/v0/RETRO.md` as historical context
4. The PRs and Linear issues linked in this packet
5. Live Git, GitHub, Linear, and tool state; live evidence outranks this packet

## Work Plan

### Phase 1: Close the review gates

Intent:
- Prove each recovered slice is independently correct before any merge.

Actions:
- Keep [PR #94](https://github.com/outfitter-dev/dispatch/pull/94) stacked on
  [PR #93](https://github.com/outfitter-dev/dispatch/pull/93).
- Run scored local review against both compatibility slices and the independent
  Claude launch slice.
- Fix every P0/P1/P2 finding with focused tests, rerun `just check`, and close
  all hosted review threads with an audit-trail reply.

Verification:
- Targeted tests for each changed module.
- `git diff --check` and clean worktrees.
- `just check` on every final branch tip.
- Hosted CI green and zero unresolved review threads.
- Local reviewer score at least 4/5 with no open P0/P1/P2.

Done when:
- all three PR tips satisfy the review contract and the retro records the rounds.

### Phase 2: Land the stack under an approval gate

Intent:
- Move reviewed work to trunk without breaking the stack or hiding external state.

Actions:
- Ask for explicit merge approval.
- Re-read live head/base SHAs, checks, mergeability, and review threads.
- Merge [PR #93](https://github.com/outfitter-dev/dispatch/pull/93), then refresh
  and merge [PR #94](https://github.com/outfitter-dev/dispatch/pull/94).
- Merge independent [PR #95](https://github.com/outfitter-dev/dispatch/pull/95)
  only after its P1 is fixed and re-reviewed.

Verification:
- GitHub reports each PR merged at the intended head SHA.
- `origin/main` contains all intended commits with no accidental merge/rebase loss.

Done when:
- the three reviewed slices are present on live trunk and Linear reflects reality.

### Phase 3: Prove the merged repository

Intent:
- Establish this machine as a trustworthy continuation environment.

Actions:
- Safely fast-forward local `main`; preserve every unrelated worktree/artifact.
- Run `uv sync`, `just check`, `uv build`, the repo package-content checker,
  `uv run dispatch --help`, and `uv run dispatchd --help`.
- Run `just test-int` (`uv run pytest -m integration`), whose harness creates an
  isolated temporary `CODEX_HOME` and ephemeral lanes; do not touch the user's
  live threads. Record auth/tool-dependent skips as skips and name the missing
  prerequisite. Do not run opt-in `just scenario` model workflows without a
  separate authorization.

Verification:
- Exact commands, counts, artifact inspection, isolation location, and cleanup
  are recorded in `RETRO.md`.

Done when:
- clean local `main` equals `origin/main` and every non-live gate passes.

### Phase 4: Prove the contained Claude launch contract

Intent:
- Validate [DIS-57](https://linear.app/outfitter/issue/DIS-57) against the real
  supported CLI without silently changing personal state.

Actions:
- Ask separately for explicit live-Claude authorization.
- Snapshot only metadata/hash for relevant settings; never print contents.
- Launch one disposable synthetic `claude --bg` session, reconcile through the
  global metadata-only roster, verify identity and pending behavior, clean up,
  and compare the metadata/hash snapshot.

Verification:
- One full provider UUID is reconciled; no prompt is retained in Dispatch logs;
  cleanup is verified; any user-level metadata change is reported, not hidden.

Done when:
- live behavior matches the internal contract or the issue/PR documents the exact
  divergence and remains gated.

### Phase 5: Resolve the terminal-host decision chain

Intent:
- Turn the remaining Claude work into decisions backed by contained evidence.

Actions:
- With explicit install/live-test authorization, evaluate Herdr for
  [DIS-62](https://linear.app/outfitter/issue/DIS-62) and cmux for
  [DIS-63](https://linear.app/outfitter/issue/DIS-63) in disposable workspaces.
- Record adopt/optional/reject evidence; do not implement the host abstraction
  during evaluation.
- Then progress [DIS-61](https://linear.app/outfitter/issue/DIS-61) ->
  [DIS-58](https://linear.app/outfitter/issue/DIS-58) ->
  [DIS-59](https://linear.app/outfitter/issue/DIS-59) ->
  [DIS-54](https://linear.app/outfitter/issue/DIS-54) ->
  [DIS-50](https://linear.app/outfitter/issue/DIS-50), one reviewable slice at a time.

Verification:
- Each evaluation has install/version evidence, synthetic scenario steps,
  restart/takeover results, cleanup proof, and a tracker decision.

Done when:
- the immediate host evaluations and dependency map are current; downstream
  implementation is either started under a new focused packet or explicitly backlog.

### Phase 6: Reconcile and archive

Intent:
- Leave code, PRs, trackers, and durable notes telling one story.

Actions:
- Reconcile GitHub and Linear status/dependencies, record residual risks, fill
  the final retro, and archive the packet per `.agents/plans/PLANNING.md`.

Verification:
- Live audit finds no stale completed issue, unresolved review debt, dirty owned
  worktree, or undocumented blocker in the completed scope.

Done when:
- the completion condition is proven and the packet is archive-ready.

## Tracker Plan

- In-goal delivery: [DIS-65](https://linear.app/outfitter/issue/DIS-65),
  [DIS-64](https://linear.app/outfitter/issue/DIS-64), and
  [DIS-57](https://linear.app/outfitter/issue/DIS-57).
- Authorized evaluation follow-ups: [DIS-62](https://linear.app/outfitter/issue/DIS-62)
  and [DIS-63](https://linear.app/outfitter/issue/DIS-63).
- Dependency chain: DIS-62 + DIS-63 -> DIS-61 -> DIS-58 -> DIS-59 -> DIS-54;
  DIS-57 + DIS-61 + DIS-54 -> DIS-50.
- Community backlog remains visible through
  [#16](https://github.com/outfitter-dev/dispatch/issues/16),
  [#17](https://github.com/outfitter-dev/dispatch/issues/17),
  [#25](https://github.com/outfitter-dev/dispatch/issues/25), and
  [#26](https://github.com/outfitter-dev/dispatch/issues/26).

## Source-Control Plan

- Model: isolated Git worktrees; Graphite ordering for the compatibility stack.
- Order: `codex/recover-studio-bridge-20260818` ->
  `dis-64-enforce-a-minimum-supported-codex-build-instead-of-an-unenforced-exact`.
- Independent branch: `dis-57-launch-claude-background-sessions-through-the-supported-cli`.
- Coordination packet branch: `codex/dispatch-back-on-track-goal`.
- PRs #93-#95 were already non-draft and CI-green before this packet began.
  Do not create or change readiness, merge, or publish without explicit approval.
  Re-read live state immediately before every remote mutation.
- The main agent owns source-control writes after this packet began. Subagents may
  inspect/review; the designated existing Sol Low implementer may write only the
  already-owned DIS-57 worktree for the explicitly delegated P1 fix.

## Retro Discipline

`RETRO.md` is the execution ledger. Update it after every implementation,
verification, review, tracker, PR, merge, packaging, or authorization change,
and immediately before handoff, merge readiness, pause, or archive.

## Validation Ladder

- Targeted: `uv run pytest <focused test paths>`
- Static/package: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy src tests`, `uv build`, package-content checker
- Full repo: `just check`
- CLI smoke: `uv run dispatch --help`; `uv run dispatchd --help`
- Isolated runtime: `just test-int`, with its temporary `CODEX_HOME` and ephemeral
  lanes; record unavailable/auth-dependent skips and never silently substitute
  `just scenario`
- Live provider: synthetic Claude ceremony only after explicit authorization

## Local Review

- Lane 1: Codex manifest provenance, schema alignment, and corpus coverage.
- Lane 2: version semantics, packaged asset integrity, doctor/daemon behavior.
- Lane 3: Claude launch side effects, retry ambiguity, identity, privacy, and bounds.

Reviewer output: score n/5, concise summary, P0-P3 findings with file/line
evidence, and a prompt to fix each actionable finding. Fix all P0/P1/P2 and
re-review before remote submission, merge readiness, or final handoff.

## Stop / Pause Rules

Stop and ask if live repo/tracker truth diverges from this plan; a public API or
scope expansion is required; merge, publication, personal/provider mutation,
secrets, or irreversible actions are needed; isolation cannot be proven; or an
unrelated verification failure persists after a focused retry.

## Handoff Audit

- [x] Objective and completion condition are checkable.
- [x] Tracker IDs, dependency order, branches, and PRs are explicit.
- [x] Preserved ignored/local-only state is excluded from cleanup.
- [x] Validation follows repo commands and explicit live-state gates.
- [x] `GOAL.md` requires transcript-visible proof and retro finalization.
- [x] Stop rules and forbidden actions are concrete.
- [x] Packet can be executed without chat history.
