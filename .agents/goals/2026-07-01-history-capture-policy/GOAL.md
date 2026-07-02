# Goal Execution Contract: History Capture Policy and DB-Backed Surfaces

Date: 2026-07-01
Status: Ready for direct start
Spec: `.agents/goals/2026-07-01-history-capture-policy/SPEC.md`
Prompt: `.agents/goals/2026-07-01-history-capture-policy/PROMPT.md`
Retro: `.agents/goals/2026-07-01-history-capture-policy/RETRO.md`
Refs: `.agents/goals/2026-07-01-history-capture-policy/REFS.md`

## Completion Horizon

`ready-pr`

Complete when:

- New stacked branches above this packet branch implement the capture
  policy, standard capture expansion, debug capture, and first DB-backed
  operator surface cutovers.
- Each milestone has focused tests, docs/skills updates where behavior changed,
  and a local-review loop with no unresolved P0, P1, or P2 findings.
- The full stack passes focused checks, CLI/MCP schema or parity checks for any
  surface changes, and `just check`.
- PRs are pushed, ready for review, and tracker/RETRO evidence matches the code.

Not complete when:

- The work only adds ADR/docs without executable capture behavior.
- Debug mode exists without retention bounds or warnings.
- Standard mode captures raw/heavy payloads without an explicit policy.
- DB-backed reads are claimed but still render only from App Server responses.
- PRs are draft, failing, unpushed, or have unresolved P0/P1/P2 review findings.

## Authority

- May create Graphite branches stacked above this packet branch.
- May commit, push, open PRs, mark PRs ready, and update PR descriptions.
- May update Linear issues/comments in the Dispatch team and create scoped
  follow-up issues when needed.
- May use bounded subagents for exploration and local-review lanes.
- May run isolated local app-server/dispatch scenarios with temp state.
- May not merge, release, publish, change storage defaults to Turso/libSQL, or
  mutate user global Codex/Claude config without explicit approval.

## Boundary

- In scope: `/Users/mg/Developer/outfitter/dispatch`, the current PR #48/#49
  stack, Dispatch Linear state, and local review artifacts under this packet.
- Out of scope: remote gateway, full Claude provider implementation, package
  release, live user thread mutation as a test strategy.
- Preserve unrelated work. If dirty state appears, identify owner/scope before
  staging or modifying it.

## Topology

Packet-backed direct execution using a milestone Graphite stack. Start from
`docs/history-capture-policy-goal` when present; otherwise start from the
current top of the PR #48/#49 stack. Create new branches above it in this order
unless execution discovers a better review split:

1. `feat/history-capture-policy`
2. `feat/history-standard-capture`
3. `feat/history-debug-capture`
4. `feat/db-backed-history-surfaces`
5. `docs/history-capture-operator-docs` only if docs are large enough to review
   separately; otherwise keep docs in the branch that changes behavior.

Each branch owns its milestone fixes. Restack after each branch and before final
submission. Do not collapse branches unless the stack is smaller and more
reviewable after implementation.

## Steps

1. Capture policy foundation
   - Outcome: Config/model/types define `minimal`, `standard`, and `debug`
     capture modes plus bounded text/payload retention settings.
   - Scope: config, policy helpers, registry models if needed, doctor/status
     visibility, tests.
   - Review gate: standing reviewer plus targeted config/privacy reviewer.
   - Verification gate: focused config/policy tests, doctor/status schema tests,
     and type/lint on touched modules.

2. Standard Tier 1 and Tier 2 capture
   - Outcome: Standard mode captures richer operational and searchable history
     facts by default without unbounded raw payload retention.
   - Scope: Codex live event indexing, `thread/read` indexing, sync/backfill
     paths, reducer idempotency, fixture updates.
   - Review gate: standing reviewer plus targeted reducer/storage reviewer.
   - Verification gate: registry/event/history tests, replay fixtures,
     idempotency/truncation tests.

3. Debug capture mode
   - Outcome: Debug mode retains raw provider payloads and reducer evidence with
     clear byte caps, truncation markers, and operator warnings.
   - Scope: provider event payload policy, raw retention tests, docs, doctor
     warning text, replay/debug fixture paths.
   - Review gate: standing reviewer plus targeted privacy/storage reviewer.
   - Verification gate: standard-vs-debug tests proving standard is bounded and
     debug captures more without exceeding configured caps.

4. DB-backed operator surfaces
   - Outcome: At least one meaningful `history`, `search`, `get`, or `list`
     surface reads from normalized DB tables when freshness is sufficient, with
     honest live-refresh or fallback behavior.
   - Scope: handlers, contracts if needed, derived CLI/MCP/schema tests, docs,
     skills.
   - Review gate: standing reviewer plus targeted surface/API reviewer.
   - Verification gate: focused handler tests, schema/parity tests, `uv run
     dispatch schema ...`, and help/schema smoke for changed commands.

5. Full-stack hardening and readiness
   - Outcome: Stack is coherent, docs/skills/Linear/PRs match, checks are green,
     and review findings are closed or explicitly accepted if P3 only.
   - Scope: restack, PR bodies, final docs pass, final full-stack review.
   - Review gate: standing reviewer plus one fresh full-stack reviewer.
   - Verification gate: final focused regression set, `just check`, PR checks,
     clean git state, Graphite stack proof.

## Reviews

Use two lanes:

- Standing reviewer: continuity across the whole stack, prior finding follow-up,
  cross-branch consistency, and final full-stack judgment.
- Targeted reviewers: fresh per milestone for config/privacy, reducer/storage,
  debug retention, and surface/API risk.

Tell reviewers to load `local-review` when available and write JSON reports
under `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/`. For every
milestone, fix all P0/P1/P2 and worthwhile P3 findings before moving to the next
branch. Return to the same reviewers for the fix loop. Record score, report
paths, blocker count, dispositions, and residual P3s in `RETRO.md`.

## Verification

- `uv run pytest` focused tests for each touched module and fixture family.
- `uv run ruff check ...` and `uv run mypy ...` on touched source during slices.
- `uv run pytest tests/fixtures/test_corpus.py tests/registry/test_store.py -q`
  when schema, fixtures, or replay paths change.
- CLI/MCP/schema/parity tests for any contract or surface change.
- `uv run dispatch schema <changed-command>` and relevant `uv run dispatch
  <command> --help` smokes for user-facing CLI changes.
- `just check` before PR readiness.
- Optional isolated scenario with temp `DISPATCH_HOME`/`CODEX_HOME`; never use
  live user state as the fixture.

## Evidence Contract

`RETRO.md` must record branch order, commits, PR URLs, Linear issue states,
checks and exact results, review report paths/scores, P0/P1/P2 closure proof,
docs/skills touched, capture-mode behavior, DB-backed surface proof, residual
risks, and forbidden-action audit.

PR bodies must describe context, branch-specific changes, tests, privacy/storage
risks, rollout notes, and how the branch fits into the stack.

## Next Move

Begin by verifying the current stack and creating `feat/history-capture-policy`
above this packet branch. If a milestone gets too large, split another branch
above the current one and record the amendment. If a check fails, narrow to a
focused repro, fix the root cause, rerun focused checks, then broaden.

## Waiting State

- Waiting on: GitHub/Graphite PR checks and review comments after PR submission.
- How to check: `gh pr view`, `gh pr checks`, `gt log --no-interactive`, and
  unresolved review thread inspection.
- Heartbeat cadence: check only after PRs exist or an external wait begins.
- Continue when: checks/reviews provide actionable state or pass.
- Stop when: external auth/tooling blocks all remaining useful work.

## Persistence

Use this packet as the resume surface. Update `RETRO.md` after every milestone,
review loop, branch split, tracker mutation, deferment, and final state.

## Stop Rules

- A required test or scenario would mutate live user Codex/Claude state.
- A product decision is needed to change completion horizon, storage default,
  merge/release authority, or raw payload defaults.
- External auth/tooling blocks every remaining useful path.
- Dirty worktree state cannot be safely attributed or preserved.

## Amendments

Amend this file when branch order, milestone scope, verification gates, or review
lanes change. Record meaningful amendments in `RETRO.md`.
