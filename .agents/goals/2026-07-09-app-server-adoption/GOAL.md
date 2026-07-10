# Goal Execution Contract: App Server Adoption

Date: 2026-07-09
Status: Active
Spec: `.agents/goals/2026-07-09-app-server-adoption/SPEC.md`
Prompt: `.agents/goals/2026-07-09-app-server-adoption/PROMPT.md`
Retro: `.agents/goals/2026-07-09-app-server-adoption/RETRO.md`
Refs: `.agents/goals/2026-07-09-app-server-adoption/REFS.md`

## Completion Horizon

Merged to `main`, tracker-reconciled, and locally dogfooded. No release or PyPI
publish is part of this horizon.

Complete when:

- PRs #73/#74 and every feasible in-scope milestone are merged in dependency
  order, `main` is synced, and the tree is clean.
- `DIS-42`, `DIS-44`, `DIS-45`, `DIS-35`/`DIS-39`, `DIS-18`, `DIS-46`, and
  `DIS-47` satisfy their acceptance criteria or carry precise, evidence-backed
  blocker comments after all independent work has continued.
- Final checks, live scenarios, docs/skills, full-stack local review, CI, and
  review-thread reconciliation are complete.

Not complete when:

- Work exists only locally, in draft/ready PRs, or on unmerged stack branches.
- Checks are green but open P0/P1/P2 findings or unresolved review threads
  remain.
- A difficult milestone is skipped without evidence while independent work is
  still possible.
- Docs, generated schemas, first-party skills, Linear state, or final branch
  cleanup are stale.

## Authority

- May commit: yes, coherent commits on non-main Graphite branches.
- May push: yes.
- May open PR: yes, with complete descriptions and issue links.
- May mark ready: yes, after local review and CI are green.
- May merge: yes, in dependency order after checks and review threads clear.
- May publish/release: no.
- Needs user approval for: secrets, account/credential changes, destructive
  user data actions, changing realtime voice scope, or a product decision that
  cannot be safely deferred.

## Boundary

- In scope: the issues and required supporting files named in `SPEC.md`.
- Out of scope: realtime voice implementation, `DIS-43`, remote mesh/gateway,
  reset-credit redemption, release/publish work, and unrelated refactors.
- Do not touch: user auth/token material, raw live audio, user-level Codex
  configuration except isolated temporary `CODEX_HOME`, or real threads with
  destructive/write probes.

## Topology

The primary agent owns execution. Use bounded subagents for protocol/codebase
exploration, implementation slices, fixture design, and independent reviews;
verify their output locally. Subagents do not commit, push, merge, publish, or
mutate Linear. Use Graphite stacked PRs, normally one issue or tightly coupled
pair per slice. A milestone must pass its review gate before the next dependent
slice begins, though independent exploration may run in parallel.

## Steps

1. Land the compatibility baseline and packet
   - Outcome: #73 then #74 merge cleanly; this packet is committed on its own
     Graphite slice and the execution stack is based on current `main`.
   - Scope: source-control and tracker reconciliation only.
   - Gate: CI/reviews clean, `gt sync`, clean tree, packet prompt validated.

2. Complete interactive request handling (`DIS-42`)
   - Outcome: all current server requests are classified, visible, and have a
     configured completion/attention/error path; no silent blocked turns.
   - Scope: client/router/events, reducer/database, config, inbox/subscriptions,
     tests/scenario, docs/skills.
   - Gate: focused tests, isolated live scenario, `just check`, 5/5 review.

3. Index canonical items (`DIS-44`)
   - Outcome: current item types and useful refs are normalized/queryable with
     replay and backward-compatibility fixtures.
   - Scope: provider event/history adapters, fixtures, query/history behavior,
     docs/skills.
   - Gate: replay/idempotency tests, live fixture capture, `just check`, 5/5.

4. Add thread topology (`DIS-45`)
   - Outcome: parent/descendant discovery and durable lineage work for managed
     and unmanaged threads without granting implicit authority.
   - Scope: typed client, database, list/inspection CLI and grouped MCP, tests,
     docs/skills.
   - Gate: nested/fork/archive fixtures, isolated smoke, `just check`, 5/5.

5. Ship usage/capacity (`DIS-35`, `DIS-39`)
   - Outcome: redacted Codex account/rate-limit/usage observations and
     first-class `dispatch usage` CLI/MCP behavior.
   - Scope: typed reads, observation persistence, authored op, docs/skills.
   - Gate: signed-out/partial/current fixtures, live-safe smoke, parity tests,
     `just check`, 5/5.

6. Make resume/backfill bounded (`DIS-18`)
   - Outcome: immediate metadata/live resume, bounded recent bootstrap, durable
     backwards continuation, observability, and stable fallbacks.
   - Scope: client/sync/index/database, tests/scenario, docs/skills.
   - Gate: restart/unchanged/huge/fallback tests, live scenario, `just check`,
     5/5.

7. Add permission profiles (`DIS-46`)
   - Outcome: live profile discovery and validated preset/config integration.
   - Scope: client/catalog/config/presets, derived surfaces, tests, docs/skills.
   - Gate: pagination/precedence/older-binary tests, live-safe smoke,
     `just check`, 5/5.

8. Add rich image inputs (`DIS-47`)
   - Outcome: text plus local/URL images work through authored `new`/`send`
     across supported delivery modes without storing image bytes.
   - Scope: contracts, queue/delivery, model capability validation, CLI/MCP,
     tests/scenario, docs/skills.
   - Gate: mixed/missing/queued/modality tests, isolated live scenario,
     `just check`, 5/5.

9. Final full-stack closeout
   - Outcome: full-stack review clean, PRs merged in order, Linear and retro
     accurate, current `main` clean and dogfooded.
   - Gate: `just check`, selected `just test-int`, all scenario evidence,
     full-stack 5/5, CI/review threads clear, `gt sync`.

## Reviews

- Run `$local-review` targeted mode after each milestone; write reports under
  `.agents/goals/2026-07-09-app-server-adoption/tmp/reviews/`, grouped by the
  milestone names recorded in `RETRO.md`.
- Fix and re-review P0/P1/P2 findings until 5/5 with zero open P0-P2. Fix easy,
  clearly worthwhile P3s; record deliberate residual P3s.
- Run an independent full-stack review before marking the final PR ready.
- Treat GitHub review comments as blockers until fixed/resolved or rebutted with
  evidence.

## Evidence Contract

- `RETRO.md` records each milestone's commits, PR, issue state, checks, scenario
  output, review report/score, amendments, risks, and next move.
- PR descriptions state context, exact behavior, tests, live evidence, risk,
  migration/config impact, and docs/skills changes.
- Final proof includes merged PRs, Linear states/comments, exact check counts,
  live-smoke outcomes, review scores, clean `main`, and forbidden-action audit.

## Verification

- Per milestone: focused `uv run pytest tests/client tests/core tests/registry`,
  `just check`, and the smallest relevant `just test-int`; create and run
  `tests/scenarios/app_server_adoption.toml` for the combined isolated live
  contract, with smaller scenario fixtures only when isolation improves proof.
- Protocol changes: refresh/compare `just app-server-manifest` and prove no
  unexplained generated drift.
- Surface changes: `uv run dispatch --help`; `uv run dispatch schema usage`,
  `send`, `new`, and affected existing routes; `--json`/jq smokes; and CLI/MCP
  parity tests.
- Prompt/goal alignment: run the goal-loop prompt checker and record the manual
  alignment audit in `RETRO.md`.

## Next Move

- If a check fails: narrow the repro, add a regression test, fix it, rerun the
  focused gate, then rerun `just check` and review.
- If progress stalls: change approach after three repeated failures, use a
  bounded exploration subagent, and continue independent milestones.
- If scope is unclear: choose the smallest behavior satisfying the issue and
  architecture; record the decision. Ask only when the choice is destructive,
  security-sensitive, or changes product authority.

## Waiting State

- Waiting on: CI, review bots/threads, Graphite mergeability, and merge queue.
- How to check: `gh pr checks 73`, `gh pr checks 74`, the same commands for the
  current milestone PR recorded in `RETRO.md`, review threads, and
  `gt log --no-interactive`.
- Heartbeat cadence: about every 10 minutes while externally waiting; stay
  quiet when state has not changed.
- Continue when: required checks pass and actionable review threads are clear.
- Stop when: an external service is unavailable through three checks and no
  independent milestone remains, or a stop rule fires.
- Last checked: not started.

## Persistence

Keep `RETRO.md` current after every milestone and before external waits. Resume
from `GOAL.md`, `RETRO.md`, Linear `DIS-41`, and the live Graphite stack. Do not
reconstruct progress from chat alone.

## Amendments

`GOAL.md` may be amended when execution reality changes. Record meaningful
changes in `RETRO.md`.

## Stop Rules

- Stop only for required credentials/secret handling, destructive user-data
  authority, an irreversible security/product decision, or repeated external
  failure after independent work is exhausted.
- A blocker in one milestone is not permission to stop the full goal; document
  it, continue independent work, and return before final closeout.
- Do not weaken tests, review gates, privacy boundaries, or contract derivation
  to manufacture completion.
