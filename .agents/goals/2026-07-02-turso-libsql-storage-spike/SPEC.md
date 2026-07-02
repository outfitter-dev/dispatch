# Goal Spec: turso-libsql-storage-spike

Date: 2026-07-02
Status: Active

## Objective

Decide, with working evidence, whether Dispatch should adopt Turso/libSQL now, later behind an optional boundary, or not at all for the registry/history substrate.

The spike must cover the concrete Dispatch pain points: write concurrency, history/search/query speed, optional vector search, multi-machine sync potential, Python packaging/runtime fit, and operational complexity. The end state should be a merged PR with the decision recorded in docs and any safe supporting spike or boundary code committed.

## Context

ADR-0023 intentionally kept SQLite/`aiosqlite` as the default backend while naming Turso/libSQL as a follow-up spike behind a small storage boundary. The history-load-shape work stabilized the current SQLite path enough that the Turso investigation can happen without using "SQLite is currently sharp" as the only reason to migrate.

Current official Turso docs split the story into several choices that Dispatch must not blur:

- `pyturso` for local embedded Turso Database, with a `sqlite3`-like synchronous API and concurrent-write/async-I/O engine claims.
- `libsql` for over-the-wire remote access and embedded replicas.
- Turso Sync for explicit local-first `push()` / `pull()` sync with cloud credentials.
- Native libSQL vector search as an optional index capability over normalized history.

The answer may be "not yet" if the current SQLite architecture is good enough and Turso adds more packaging or operational risk than value. That still counts as success if it is evidence-backed and documented.

## Scope

### In

- Inspect current registry/history storage shape and identify the smallest viable storage boundary, if one is actually useful.
- Research current official Turso/libSQL/Python/sync/vector docs and record the important compatibility facts.
- Build a bounded spike, benchmark, or fixture-backed prototype that compares Dispatch's SQLite baseline against at least one Turso/libSQL path when feasible.
- Keep SQLite/`aiosqlite` as the default backend unless the final decision explicitly recommends a later migration.
- Update ADR/docs/research/skills only where the spike makes current guidance stale or incomplete.
- Update Linear issue `DIS-8` with the final decision and evidence summary.
- Submit, review, and merge the PR if the evidence and checks are clean.

### Out

- Making Turso/libSQL the production default in this goal.
- Moving real user registry data, live `~/.codex`, or user secrets into Turso Cloud.
- Creating a remote/cloud credential flow.
- Publishing a PyPI release.
- Broad provider or Claude backend work.
- Introducing a provider-generalized database framework if a smaller storage seam is enough.

## Source Of Truth

- `docs/adrs/0023-provider-event-log-and-history-index.md` - current architectural decision and Turso boundary direction.
- `.agents/goals/2026-07-02-history-load-shape/RETRO.md` - proof that the current SQLite load-shape stabilization landed before this spike.
- `src/outfitter/dispatch/registry/store.py` - current SQLite registry/history implementation and transaction behavior.
- `tests/fixtures/` and `tests/registry/` - fixture and storage test conventions that any backend proof should respect.
- Official Turso docs:
  - `https://docs.turso.tech/libsql`
  - `https://docs.turso.tech/sdk/python/quickstart`
  - `https://docs.turso.tech/sync/usage`
  - `https://docs.turso.tech/features/ai-and-embeddings`
- Linear `DIS-8` - tracker issue for the Turso/libSQL spike.

## Acceptance Criteria

- The final PR records a clear recommendation: adopt now, optional later, defer, or reject.
- The recommendation answers each decision question in the "Decisions" section with evidence, not intuition.
- At least one practical proof exists: a spike script, focused fixture test, benchmark, or documented failed installation/runtime probe.
- Any production code added is small, tested, and does not change the default backend.
- `just check` passes before ready/merge, unless a hard external blocker is recorded with exact failed command output.
- Local review loops find no unresolved P0/P1/P2 issues.
- Linear `DIS-8` is updated with the outcome, PR link, checks, and any follow-up issues.
- The branch is merged and local `main` is clean/synced if merge authority remains valid.

## Decisions

The goal runner must walk these questions explicitly at the beginning, after the prototype, and in the final retro:

1. What exact problem would Turso solve for Dispatch today that SQLite/`aiosqlite` does not already solve after the history-load-shape fixes?
2. Is there a small storage boundary that can cover registry/history operations without turning the codebase into a provider-abstraction project?
3. Which Turso path is relevant for Dispatch first: `pyturso` local embedded, `libsql` remote/embedded replicas, Turso Sync, vector search only, or none?
4. Does the Python API fit Dispatch's async daemon without blocking the event loop or forcing a large executor wrapper?
5. What are the dependency, packaging, wheel, platform, and installation risks for Dispatch users?
6. Can the current fixture and registry tests be run meaningfully against a second backend, or does the store shape need refactoring first?
7. Does vector search belong in this spike, or should it remain an optional later index over normalized `thread_items` and summaries?
8. Would Turso Cloud or sync require a security/config story that is outside v0 local control-plane scope?
9. If adoption is deferred, what precise trigger should cause Dispatch to revisit Turso?

## Risks

- Turso's Python story is moving quickly; docs or packages may change underneath the spike.
- A synchronous drop-in API may look easy while hiding event-loop blocking risk.
- The desire for vectors or multi-machine sync could tempt a backend migration before the operational model is ready.
- Building an abstraction layer too early could add more complexity than the current store warrants.
- Benchmarks on tiny fixtures can mislead; the goal should prefer shape and correctness evidence over fake precision.
