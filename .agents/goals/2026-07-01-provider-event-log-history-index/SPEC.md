# Goal Spec: Provider Event Log and History Index

Date: 2026-07-01
Status: Ready for execution

## Objective

Implement ADR-0023 as a Codex-first provider event and history substrate for
Dispatch, then use it to make runtime status, history, search, subscriptions,
and future Claude support more reliable and testable.

## Context

Dispatch has already found real friction around live delivery proof, thread
state, history, and provider-specific behavior. The current Codex path can read
App Server history and live events, but the product needs a normalized substrate
that can be replayed, queried, tested, and later fed by Claude hooks without
copying every provider-specific detail through every surface.

ADR-0023 and the Linear project define the desired direction: dogfood the
schema and reducers on Codex first, keep SQLite as the default local store, and
spike Turso/libSQL only behind a storage boundary.

## Scope

### In

- Provider-neutral event and history schema for Codex-first dogfooding.
- Storage boundaries and migrations for normalized event/history data.
- Codex App Server event persistence and reducers for runtime lane state,
  turns, items, and message receipts.
- Progressive JSONL backfill feeding the same normalized history index.
- DB-backed reads where they improve history, search, status, and
  subscriptions without broad surface churn.
- Replay fixtures, focused gates, and documentation or skill updates that
  prevent hand-wired drift.
- Linear issue updates for DIS-1 through DIS-10 as implementation reality
  changes.

### Out

- Making Turso/libSQL the default runtime store.
- Fully implementing Claude provider support before the Codex substrate is
  proven.
- Remote mesh or Cloud Gateway runtime implementation.
- Publishing a package release or merging PRs without explicit approval.
- Touching live user Codex state in tests.

## Source Of Truth

- `docs/adrs/0023-provider-event-log-and-history-index.md` - architecture
  decision for the event log and history index.
- Linear project `Provider Event Log and History Index` - issue breakdown and
  dependencies for DIS-1 through DIS-10.
- `AGENTS.md` - project lexicon, contract derivation rules, and test policy.
- `.claude/rules/contracts.md` - no-drift contract and surface projection
  requirements.
- `.claude/rules/client.md` - Codex App Server access boundary.
- `docs/research/app-server-verification.md` - verified App Server primitives
  that the Codex provider path must respect.

## Acceptance Criteria

- The repo has a coherent Codex-first implementation of provider events,
  normalized history, and reducers at the storage boundary.
- New behavior is covered by focused tests and replay fixtures, and the fixture
  suite guards against future drift.
- Surface changes stay derived from contracts rather than hand-wired one-offs.
- Docs and first-party skills describe the new DB-backed behavior accurately.
- `just check` passes, or any skipped check has a precise stop-rule reason.
- Local review finds no unresolved P0, P1, or P2 issues.
- PRs are pushed and ready for review with Linear issues updated.
- `RETRO.md` records checks, review results, issue state, residual risks, and
  explicit deferments.

## Decisions

- Completion horizon is `ready-pr`.
- Use a single goal packet with milestone execution; split branches or PRs only
  if reviewability demands it.
- SQLite with `aiosqlite` remains the default local store during this goal.
- Turso/libSQL work is a spike and boundary proof unless explicitly promoted.
- Claude hook mapping is a design and spike follow-up after Codex dogfooding.

## Risks

- The substrate could become too broad if it tries to replace every existing
  registry read in one pass.
- Live App Server behavior may differ from fixtures; isolate live scenarios and
  record mismatches.
- DB writes can become scattered unless storage APIs and reducer ownership are
  explicit.
- Existing dirty worktree changes may belong to another in-progress branch and
  must not be overwritten.
