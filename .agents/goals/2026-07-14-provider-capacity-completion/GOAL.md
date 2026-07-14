# Goal Execution Contract: Provider Capacity Completion

Date: 2026-07-14
Status: Active
Spec: `.agents/goals/2026-07-14-provider-capacity-completion/SPEC.md`
Prompt: `.agents/goals/2026-07-14-provider-capacity-completion/PROMPT.md`
Retro: `.agents/goals/2026-07-14-provider-capacity-completion/RETRO.md`
Refs: `.agents/goals/2026-07-14-provider-capacity-completion/REFS.md`

## Completion Horizon

`ready-pr`

Complete when:

- The ordered DIS-36, DIS-37, and DIS-38 Graphite PR stack is pushed, CI-green, non-draft, and has zero open P0/P1/P2 review findings.
- Each issue's acceptance contract is either implemented or explicitly reconciled in Linear with evidence; DIS-34 remains In Progress until merge.
- `RETRO.md` contains verification, review, privacy/non-mutation audit, tracker state, branch/PR order, and resume proof.

Not complete when:

- Only local checks pass, a draft PR exists, a provider works only on the author's machine, or tracker state changes without matching code/evidence.
- Any branch has open CI failures, unresolved review threads, open P0/P1/P2 findings, or unverified secret/privacy behavior.
- DIS-36 works but DIS-37/DIS-38 remain vague follow-ups outside this packet.

## Authority

- May commit: yes, scoped packet/code/tests/docs changes on issue-owned branches.
- May push: yes, the ordered Graphite stack.
- May open PR: yes, draft first with Conventional Commit titles and descriptive bodies.
- May mark ready: yes, only after local review and CI are clean.
- May merge: no.
- May publish/release: no.
- May mutate tracker: yes, DIS-34 and DIS-36 through DIS-38 status/comments when backed by current evidence.
- Needs user approval for: merge, release, publish, private Claude endpoints, live Claude configuration edits, or scope beyond this issue family.

## Boundary

- In scope: provider observations, Claude read-only probes, statusline snapshot ingestion, usage refresh/rendering, fixtures/tests/docs, packet, PRs, and scoped Linear reconciliation.
- Out of scope: routing policy, mesh transport, remote control, general policy engine, private OAuth usage calls, broad backlog cleanup.
- Do not touch: live auth files, tokens, keychain/cookies, raw transcripts, `~/.claude` configuration, or unrelated user worktree changes.

## Topology

Coordinator-led Graphite milestone stack. The primary agent owns synthesis, edits, tracker mutations, commits, PRs, and final decisions. Bounded subagents may perform read-only audits and local-review lanes.

## Steps

1. Reconcile the executable contract
   - Outcome: current code and DIS-34 family tracker state agree on the remaining work; DIS-34 and DIS-36 are moved to active states only when execution begins.
   - Scope: DIS-34 through DIS-40, current PRs/code, ADR-0023; DIS-40 remains deferred unless supported surfaces fail.
   - Gate: `RETRO.md` records the reconciliation and no unrelated tracker mutation is made.

2. DIS-36 Claude account and runtime probes
   - Outcome: provider-neutral Claude auth/runtime observation, aggregate privacy-safe runtime summary, independent freshness, and `usage` refresh integration.
   - Branch: `dis-36-add-claude-account-and-runtime-probes` from `main`.
   - Gate: focused tests, manual read-only smoke, standing plus targeted review clean, `just check` green, draft PR CI green, then mark ready.

3. DIS-37 Claude statusline capacity snapshots
   - Outcome: atomic bounded capture beneath `DISPATCH_HOME`, merge-only reader, five-hour/seven-day windows, explicit missing/stale states, setup docs without automatic config mutation.
   - Branch: `dis-37-capture-claude-capacity-from-statusline-snapshots` stacked on DIS-36.
   - Gate: synthetic capture/read smoke, privacy/non-mutation audit, focused tests, standing plus targeted review clean, `just check` and CI green, then mark ready.

4. DIS-38 observation-store reconciliation
   - Outcome: current provider-neutral model, component TTL/provenance, mesh-compatible shape, and latest-only persistence decision are fully tested and reflected in Linear/docs.
   - Branch: `dis-38-store-normalized-provider-observations-for-mesh-heartbeats` stacked on DIS-37.
   - Gate: regression/surface tests, full-stack standing and fresh review clean, `just check`, stack/CI proof, then mark all PRs ready and update tracker to In Review.

## Reviews

- Reuse one standing reviewer across all milestones for contract continuity and prior-finding follow-up.
- Use a fresh targeted reviewer per milestone for provider/privacy, snapshot/atomicity, and final model/tracker risks.
- Reviewers load `local-review` and write JSON to `.agents/goals/2026-07-14-provider-capacity-completion/tmp/reviews/<reviewer>/<round>.json`.
- Fix all P0/P1/P2 findings and reasonable P3s on the owning branch; re-review with the same reviewer before moving up.
- Final stack gate uses the standing reviewer plus one fresh independent full-stack reviewer.

## Evidence Contract

- Record exact commands/results, manual smoke output with sensitive values omitted, changed file paths, review report summaries, issue/status mutations, branch/PR order, CI URLs/state, and unresolved risks in `RETRO.md`.
- PR bodies must state context, changes, tests, privacy posture, risks, and Linear mapping.
- Final transcript must prove the `ready-pr` horizon and clearly state that merge/release/publish were not authorized.

## Verification

- Baseline/focused: `uv run pytest tests/core/test_capacity.py tests/registry/test_store.py -q` plus new Claude-focused tests.
- Surfaces: `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_parity.py -q`.
- Full gate: `just check` on each branch and after final restack.
- Manual smoke: `uv run dispatch usage --provider claude --json`; verify no raw email, org id, cwd, session id, or raw daemon status; exercise statusline capture under temporary `DISPATCH_HOME`; confirm `~/.claude` is unchanged.
- Stack/remote: `gt log --stack`, `gh pr checks`, unresolved review-thread inspection, and clean `git status`.
- Prompt/goal alignment: run goal prompt checker, goal-loop doctor, and record a direct comparison in `RETRO.md`.

## Next Move

- If a check fails: isolate the narrow failing provider/model/surface test, fix on the owning branch, then rerun focused and aggregate gates.
- If progress stalls: after three failures, change approach, shrink the repro, and record evidence instead of repeating the same command.
- If scope is unclear: preserve supported-path/privacy boundaries, continue independent work, and ask only when the choice would expand authority or materially change the contract.

## Waiting State

- Waiting on: GitHub CI and review feedback after each draft PR.
- How to check: `gh pr checks <number>` and GitHub review threads; use `gt log --stack` for branch order.
- Heartbeat cadence: poll at roughly 2-5 minute intervals while actively waiting, reporting only state changes or blockers.
- Continue when: required checks pass and review has zero open P0/P1/P2 findings.
- Stop when: authentication/access is unavailable, an external check repeatedly fails for reasons outside the repo after diagnosis, or user authority is required.
- Last checked: not started.

## Persistence

- Update `RETRO.md` before each branch transition, external wait, handoff, or goal amendment.
- Resume surface: read `RETRO.md`, then `GOAL.md`, live Linear issues DIS-34/DIS-36/DIS-37/DIS-38, `gt log --stack`, open PRs, and `git status`.

## Amendments

`GOAL.md` may be amended when execution reality changes. Record meaningful changes in `RETRO.md`; do not weaken the horizon, verification, review, privacy, or authority contract without user approval.

## Stop Rules

- Required Claude CLI behavior cannot be verified through supported read-only surfaces and continuing would require private endpoints or auth-file access.
- A necessary change would expose or persist secrets/raw identity/session data beyond the approved normalized fields.
- Repo/tracker/branch state conflicts with unrelated user work and cannot be isolated safely.
- Merge, release, publish, live Claude config mutation, or material scope expansion becomes necessary.
