# Goal Execution Contract: Provider Event Log and History Index

Date: 2026-07-01
Status: Ready for direct start
Spec: `.agents/goals/2026-07-01-provider-event-log-history-index/SPEC.md`
Prompt: `.agents/goals/2026-07-01-provider-event-log-history-index/PROMPT.md`
Retro: `.agents/goals/2026-07-01-provider-event-log-history-index/RETRO.md`
Refs: `.agents/goals/2026-07-01-provider-event-log-history-index/REFS.md`

## Completion Horizon

`ready-pr`

Complete when:

- Codex-first provider event/history work is implemented in coherent slices.
- Focused tests, replay fixtures, schema or parity checks, and `just check`
  pass.
- Local review has no unresolved P0, P1, or P2 findings.
- Documentation, usage docs, and first-party skills are updated for changed
  behavior.
- PRs are pushed and ready for review, with Linear issues updated to match the
  delivered and deferred scope.
- `RETRO.md` contains final proof against this contract.

Not complete when:

- Only ADRs, tracker issues, or packet files exist.
- The DB schema exists but no Codex path dogfoods it.
- History/status/search/subscription surfaces still rely on stale hand-wired
  assumptions after behavior changes.
- Checks are green but open P0, P1, or P2 review findings remain.
- Local work is uncommitted or unpushed.

## Authority

- May commit: yes, for scoped source, tests, docs, skills, and packet updates.
- May push: yes, to working branches for this goal.
- May open PR: yes, draft or ready PRs for this goal.
- May mark ready: yes, after local checks and review gate pass.
- May merge: no, unless Matt explicitly authorizes it later.
- May publish/release: no, unless Matt explicitly authorizes it later.
- May update Linear: yes, for DIS-1 through DIS-10 and project notes.
- Needs user approval for: merge, release, publish, live user state mutation,
  changing completion horizon, or making Turso the default store.

## Boundary

- In scope: `/Users/mg/Developer/outfitter/dispatch`, Linear Dispatch project
  `Provider Event Log and History Index`, and local review artifacts under this
  packet.
- Out of scope: user-level Codex or Claude configuration, live `~/.codex` state,
  remote gateway runtime, and package publishing.
- Do not touch: unrelated dirty files except to preserve or carefully build on
  them when they are directly required for this goal.

## Topology

Packet-backed direct execution with milestone slices. Use bounded subagents for
exploration or review, but keep architecture decisions, source-control writes,
and tracker mutations centralized.

## Steps

1. Schema and replay gates
   - Outcome: DIS-1 and DIS-7 define storage boundaries, normalized schema,
     migration shape, and replay fixtures that fail before implementation and
     guard the reducer contract.
   - Scope: registry models, store boundaries, fixture builders, and tests.
   - Gate: focused storage and fixture tests pass; no live user state touched.

2. Codex live event ingestion and reducers
   - Outcome: DIS-2 and DIS-3 persist Codex App Server live events into
     normalized provider events and reduce them into lane runtime state, turns,
     items, and message receipts.
   - Scope: client/core/registry paths that already own App Server access.
   - Gate: focused core tests prove ordering, idempotency, receipt semantics,
     and failure behavior.

3. Backfill and sync
   - Outcome: DIS-4 and DIS-5 backfill normalized turns/items from Codex
     `thread/read` and progressive JSONL sync, preserving top-of-file metadata
     and recency-first behavior.
   - Scope: sync/backfill code, fixtures, and docs.
   - Gate: replay/backfill tests cover long-lived threads, duplicate events,
     partial sync, and restart behavior.

4. DB-backed operator surfaces
   - Outcome: DIS-6 moves the useful parts of history, search, status, and
     subscriptions toward normalized reads while keeping CLI/MCP projection
     derived from contracts.
   - Scope: contracts, handlers, derived CLI/MCP tests, docs, and skills.
   - Gate: schema/parity tests pass and `dispatch --help` or command schemas do
     not drift from the op registry.

5. Provider and storage follow-through
   - Outcome: DIS-8 and DIS-9 document or spike Turso/libSQL and Claude hook
     mapping only to the depth needed to prove the boundary or create follow-up
     implementation issues.
   - Scope: docs, ADR notes, small isolated spikes, Linear follow-ups.
   - Gate: no default storage migration and no Claude runtime promise without
     evidence.

6. Docs, review, and PR readiness
   - Outcome: DIS-10 and final polish align ADRs, usage docs, skills, PR body,
     and Linear state with the implemented behavior.
   - Scope: all touched docs and packet evidence.
   - Gate: `just check`, local review, clean git state, pushed PRs ready.

## Reviews

- Run local review after each meaningful slice.
- Fix all P0, P1, and P2 findings before moving upward.
- Fix easy or high-value P3 findings; otherwise record accepted residual P3s in
  `RETRO.md` and Linear.
- Before PR readiness, run a final full-stack review and record the score,
  open blocker count, and report path or summary.

## Evidence Contract

- `RETRO.md` must list implemented issues, commits, PRs, Linear states, checks,
  review outcomes, residual risks, and forbidden-action audit.
- PR descriptions must include context, changes, tests, risks, and rollout
  notes.
- Linear issues must not claim completion beyond what shipped.

## Verification

- Focused pytest for each touched module or fixture family.
- CLI/MCP schema and parity checks for any surface change.
- `just check` before marking PRs ready.
- Isolated live scenario only when useful and safe; do not use real user Codex
  state as a test fixture.
- Prompt/goal alignment: `PROMPT.md` must include sequence, loop, checks,
  review gate, authority, stop rules, definition of done, not-done states, and
  evidence contract.

## Next Move

- If a check fails: narrow to the failing fixture or module, fix the root cause,
  rerun focused checks, then rerun the broader gate.
- If progress stalls: reduce to a smaller coherent slice, record the deferral,
  and keep the ready-pr horizon unless a stop rule fires.
- If scope is unclear: prefer the smallest Codex-first substrate improvement,
  update `GOAL.md` and `RETRO.md`, and continue.

## Waiting State

- Waiting on: PR CI or review only after PRs are opened.
- How to check: GitHub PR checks and local `gh pr view` or `gh pr checks` when a
  PR exists.
- Heartbeat cadence: none during local-only work; check only when external PR
  state is created.
- Continue when: checks or review state are clear enough to fix or mark ready.
- Stop when: external auth, CI infrastructure, or review access blocks all
  remaining work.
- Last checked: not started.

## Persistence

Use this goal packet as the resume surface. Update `RETRO.md` after each slice,
review round, meaningful deferment, tracker mutation, and final state.

## Amendments

`GOAL.md` may be amended when execution reality changes. Record meaningful
changes in `RETRO.md`.

## Stop Rules

- A required operation risks mutating live user Codex state.
- The worktree has an irreconcilable conflict with unrelated user changes.
- External auth or service access blocks every remaining useful path.
- A product decision is required to change completion horizon, storage default,
  merge authority, or release authority.
