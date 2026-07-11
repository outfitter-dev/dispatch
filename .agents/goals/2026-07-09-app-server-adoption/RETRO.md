# Execution Retro: App Server Adoption

Date started: 2026-07-09
Date finalized: pending
Status: Active - DIS-18 submission
Spec: `.agents/goals/2026-07-09-app-server-adoption/SPEC.md`
Goal: `.agents/goals/2026-07-09-app-server-adoption/GOAL.md`
Prompt: `.agents/goals/2026-07-09-app-server-adoption/PROMPT.md`
Refs: `.agents/goals/2026-07-09-app-server-adoption/REFS.md`

## Summary

- Objective: land the clear App Server 0.144 adoption work.
- Completion horizon: merged, tracker-reconciled, dogfooded, clean `main`.
- Authority used: Linear planning plus a dedicated Graphite packet commit.
- Outcome: preparation, DIS-42, DIS-44, DIS-45, DIS-35, and DIS-39 are merged;
  DIS-18 is locally complete and awaiting submission.
- Tracker/PR/source-control state: PRs #73-#81 merged; DIS-18 is on its dedicated
  Graphite branch and GitHub currently has no open PRs. Independent release PR
  #81 merged after this branch started, so DIS-18 must restack onto current main
  before submission; release/publish work remains outside this goal.
- Verification: prompt gate passed at 3,752 characters with no placeholders.
- Review state: earlier milestones are clean; DIS-18 round 6 is 5/5 with all
  round 1-5 findings verified fixed and no open P0-P2 findings.
- Remaining risks: partial provider history views,
  live scenario entitlement/capacity, and remaining milestone stack order.

## Readiness

- Prompt checked: passed at 3,752 characters with no unresolved placeholders.
- Goal/prompt alignment checked: passed manually after the final prompt gate.
- Review blockers: none for preparation.
- Verification blockers: none known.
- Tracker blockers: none; issues and Linear adoption plan exist.
- Authority blockers: release/publish intentionally excluded.
- Next action: submit DIS-18, reconcile hosted checks and review, then merge the milestone.

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-09 | Initial execution contract | User requested an ambitious direct-start goal | Matt |

## Execution Log

```text
2026-07-09 - Preparation
- Changed: Linear realtime voice document and App Server adoption packet.
- Verified: live repo/PR/Graphite state and current Linear issue structure.
- Result: prompt validated and packet committed as `cb9fd57`.
- Next: execute milestone 1.
- Blockers: none.

2026-07-10 - Baseline merge and execution start
- Changed: repaired PR #75 description, merged #73 then #74 through Graphite,
  force-synced Graphite metadata, and reparented #75 to current `main`.
- Verified: #73/#74 required checks green, no open review threads, both merged;
  local `main` fast-forwarded to `13bd959`.
- Result: App Server 0.144 compatibility baseline is on `main`; `DIS-41` and
  `DIS-42` moved to In Progress.
- Next: implement interactive request completeness on a new slice above #75.
- Blockers: none.

2026-07-10 - DIS-42 interactive request implementation and first review
- Changed: classified every stable server request; added string JSON-RPC ids, a generic
  request stream/responder, connection-scoped durable request rows, atomic response claims,
  restart recovery, explicit policy/timeout behavior, inbox/subscription attention, provider
  events, generic CLI/MCP list/respond ops, config/docs/skill updates, and an isolated live
  scenario.
- Verified: `just check` (452 passed, 12 deselected, wheel/sdist contents); real App Server
  approval, plan-mode `requestUserInput`, and stdio MCP elicitation all completed through the
  Dispatch request manager (3 passed in 91.72s); `interactive_requests.toml` scenario passed;
  regenerated 0.144 schemas matched the checked-in manifest except the installed binary's
  `0.144.0-alpha.4` version label.
- Review: code and surface round 1 both scored 3/5. Fixed atomic duplicate detection,
  reconnect/send-failure attention cleanup, all-category approval trigger/subscription routing,
  local request ids in subscription payloads, nested response validation, elicitation capability
  advertisement, and missing live/scenario proof.
- Result: focused and full local gates green; both targeted re-reviews are 5/5 clean.
- Next: submit the DIS-42 stack slice, reconcile CI/review, then merge the milestone.
- Blockers: none.

2026-07-10 - DIS-44 canonical item ingestion implementation
- Changed: added one canonical 0.144 item normalizer for live events, replay, and raw
  history projection; projected full item/started and item/completed payloads; migrated
  normalized item storage to schema v15; exposed normalized query output; added explicit
  refs, generated-manifest discriminants, fixtures, docs, skill guidance, and a live scenario.
- Correctness: preserved richer rows across sparse/stale updates, stable replay timestamps,
  composite ref identities, unknown future items, minimal-capture history, and additive
  replay. A real scenario proved thread/read can omit a live commandExecution and use
  different persisted ids; destructive snapshot pruning was removed from provider replay.
- Verified: focused convergence/query/registry suite passed (162 tests); `just check`
  passed (461 tests, 12 live tests deselected, strict mypy/Ruff and package build); manifest
  regeneration matched 0.144 except the installed alpha version label; derived query schema
  exposes normalized fields; real low-effort Spark canonical-item scenario passed.
- Review: code round 1 scored 2/5 with three P1 and one P2; surface round 1
  scored 3/5 with one P1 and two P2. Fixed additive-summary consistency,
  normalized-field bounds/redaction, monotonic payload retention, failed-command
  diagnostics, and canonical history projection/filter coverage. Round 2 found
  two additional ref-filter P2s and two surface P1/P2s; those were fixed with
  preserved thread/argument refs, child-agent summary rollups, complete docs,
  and a generic authored-input CLI parity guard. Code and surface round 3 are 5/5 clean.
- Next: resolve review findings, obtain 5/5, commit/submit/merge DIS-44, reconcile Linear,
  then begin DIS-45.
- Blockers: none.

2026-07-10 - DIS-44 merge and DIS-45 topology implementation
- Merged: PR #77 as `bce4243b23cdd5139ecc8cf1a3300776a7189233`; Linear DIS-44
  moved to Done and Graphite returned to clean `main` before the next slice.
- Changed for DIS-45: added typed 0.144 parent/ancestor list filters and tagged
  thread sources; schema-v16 provider thread observations and bounded topology;
  lifecycle tombstones; cache projections for managed and unmanaged threads;
  derived list/get CLI and MCP fields; protocol fixtures and manifest guards;
  ADR, research, usage, and skill guidance.
- Correctness: provider topology is independent from lane authority; discovery
  never creates lanes; parent and fork edges stay distinct; managed threads are
  excluded from unmanaged discovery; ordinary reads are cached and explicit
  get refresh is bounded.
- Verified: focused client/registry/core/surface tests passed; final `just check`
  passed with 486 tests and 13 live tests deselected, strict mypy/Ruff, build,
  and package contents. A real isolated 0.144 App Server fork test passed in
  8.44 seconds and proved forks are excluded from parent/ancestor results.
- Review note: an attempted independent subagent review could not run because
  the account usage limit was reached. It is not counted as review evidence;
  the local structured review continues without waiting on it.
- Review: code round 1 found three P2s: per-thread commits, stale provider
  lifecycle during sync reconciliation, and missing batch rollback. All were
  fixed. Code round 2 and surface round 1 are 5/5 clean with zero open P0-P2.
- Next: commit, submit, reconcile CI/review, merge, and update Linear.
- Blockers: none.

2026-07-11 - DIS-45 submission
- Committed: `a8d7e01` (`feat: persist provider thread topology`) and opened
  draft PR #78 against `main` with a complete context, behavior, verification,
  and risk description.
- Hosted verification: CI, CodeQL actions, CodeQL Python, and the aggregate
  CodeQL check all passed. GitHub reports a clean merge state with no review
  threads or actionable comments.
- Tracker: DIS-45 remains In Progress with the PR and full local/live evidence
  recorded. It will move to Done after merge proof.
- Next: amend this retro evidence, resubmit, confirm the final commit's checks,
  mark ready, merge through Graphite, sync clean main, and close DIS-45.

2026-07-11 - DIS-45 merge and DIS-35 capacity substrate
- Merged: PR #78 as `d1aa3428ac9aa5f6be15fc176e033dfc6e2f7559` after final CI
  and CodeQL passed with no review threads. Graphite synced clean `main`, removed
  the merged branch, and Linear DIS-45 moved to Done with final proof.
- Changed for DIS-35: typed account, multi-bucket rate-limit, reset-credit, and
  historical usage reads; current/signed-out fixtures and generated schema
  guards; schema-v17 provider-neutral replace-in-place capacity observations;
  masked account labels and fingerprinted account/reset-credit identities;
  normalized account notification stream and reactor refresh.
- Live proof: a real local 0.144 App Server probe called all three read methods
  without a model turn and printed only the redacted Dispatch observation.
- Review: round 1 found two P2 freshness/merge issues. Component and per-window
  timestamps now prevent a rate push from refreshing unrelated facts, and
  sparse pushes preserve unmentioned windows. Round 2 is 5/5 clean with zero
  open P0-P2; final `just check` passed with 497 tests and 14 live deselected.
- Next: complete DIS-35 verification and review, then commit its Graphite slice
  before building the derived DIS-39 usage surface above it.
- Blockers: none.

2026-07-11 - DIS-35/DIS-39 usage and capacity merged
- DIS-35 merged through PR #79 as `12da706`; the typed account, rate-limit,
  reset-credit, and usage observations retain redacted identity and independent
  component/window freshness. Linear DIS-35 is Done with final proof.
- DIS-39 merged through PR #80 as `dd0f85e`; one authored `usage` op derives
  the top-level CLI and existing grouped daemon-read MCP action, with compact
  defaults, optional daily history, host/provider/config filters, and explicit
  stale/partial states. Linear DIS-39 is Done with final proof.
- Dogfood: an isolated daemon using copied read-only auth refreshed real 0.144
  data through `dispatch usage --json`; jq verified masked/fingerprinted
  identity, compact output, and absence of sensitive keys.
- Review: DIS-35 round 2 and DIS-39 round 2 are both 5/5 clean with zero open
  P0-P2. Final DIS-39 `just check` passed with 503 tests and 14 live tests
  deselected; hosted CI, CodeQL, and Graphite mergeability passed for both PRs.
- Reconciliation: `gt sync --force` returned the worktree to clean `main` and
  removed both merged branches. The independent draft release PR #81 was left
  untouched because release/publish work is outside this goal.
- Next: implement DIS-18 bounded resume and incremental history backfill.
- Blockers: none.

2026-07-11 - DIS-18 bounded resume and incremental history backfill
- Implemented typed `initialTurnsPage`, `thread/turns/list`, and
  `thread/items/list` client support plus generated-manifest guards. The
  installed `0.144.0-alpha.4` binary advertises item paging but returns
  `-32601`, so Dispatch uses an exact-turn full-page fallback without
  persisting content that exceeds the remaining item/byte budget.
- Sync now reconciles recent turns before older backfill; persists crash-safe
  turn/item direction, cursor, observation, and bounded cycle-guard state;
  resumes unread complete-line JSONL offsets; and shares one `max_seconds`
  deadline across provider calls. Explicitly observed attached lanes resume
  after daemon restart independently of paging capability.
- Review: round 1 found three P1 and three P2 issues; round 2 found six P1 and
  five P2 issues; round 3 found four P1 and five P2 issues. Repairs now include
  one operation deadline, stable observation fallback, zero-budget and
  oversized-record JSONL state, serialized live event writes, an unmanaged
  scenario, current architecture docs, and a 199-line coordinator split into
  explicit turn/item phases over one progress object. Round 4 then proved that
  an unguarded queue write could commit another task's active transaction; every
  public registry mutation now passes through one task-reentrant write boundary
  with rollback-isolation coverage. Round 5 then found that another task could
  still read uncommitted state through the shared connection; every public
  registry access now uses the same task-reentrant boundary.
- Verification: `just check` passed with 560 tests and 15 live tests deselected;
  package build and contents passed. The targeted real App Server integration
  passed, and `bounded_history_sync.toml` created a persisted unmanaged Codex
  thread before Dispatch started, then proved raw-id registration, bounded turn
  and message indexing, observation, stable re-sync, and transcript access.
- Next: commit/submit DIS-18, reconcile hosted checks and Linear, merge through
  Graphite, then continue DIS-46.
- Blockers: none.

2026-07-11 - DIS-18 final local review
- Review: round 5 re-verified the full prior-finding set but found one P1:
  serializing public mutations prevented cross-task commits, yet a concurrent
  read on the same SQLite connection could still observe uncommitted state.
  The shared task-reentrant boundary now covers every public registry access,
  with an introspection guard and dirty-read regression. Round 6 is 5/5 with no
  open P0-P2 findings; these results were returned in chat as requested rather
  than written as another scratch report.
- Verified: `just check` passed with 560 tests and 15 live tests deselected; a
  focused backfill/sync/handler/registry/supervisor/scenario/surface suite passed
  285 tests; derived `sync` schema/help, scenario dry-run, package contents, and
  `git diff --check` passed.
- Next: commit and submit DIS-18, reconcile hosted checks and review, merge
  through Graphite, update Linear, then continue DIS-46.
- Blockers: none.
```

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| prep | prompt/goal alignment | manual | 5/5 | clean | 0 | Core contract is present in prompt |
| DIS-42 r1 code | protocol/concurrency/restart/security | `tmp/reviews/dis-42/code-round-1.json` | 3/5 | changes requested | 3 | Two P1, one P2; fixes applied |
| DIS-42 r1 surface | CLI/MCP/config/docs/evidence | `tmp/reviews/dis-42/surface-round-1.json` | 3/5 | changes requested | 3 | One P1, two P2; fixes applied |
| DIS-42 r2 code | protocol/concurrency/restart/security | `tmp/reviews/dis-42/code-round-2.json` | 5/5 | clean | 0 | All prior findings verified fixed; 63 focused tests |
| DIS-42 r2 surface | CLI/MCP/config/docs/evidence | `tmp/reviews/dis-42/surface-round-2.json` | 5/5 | clean | 0 | All prior findings fixed; 120 focused tests |
| DIS-44 r1 code | protocol/convergence/storage/privacy | `tmp/reviews/dis-44/code-round-1.json` | 2/5 | changes requested | 4 | Three P1 and one P2; fixes applied |
| DIS-44 r1 surface | CLI/MCP/docs/fixtures/evidence | `tmp/reviews/dis-44/surface-round-1.json` | 3/5 | changes requested | 3 | One P1 and two P2; fixes applied |
| DIS-44 r2 code | protocol/convergence/storage/privacy | `tmp/reviews/dis-44/code-round-2.json` | 4/5 | changes requested | 2 | Two P2 ref-filter mismatches; fixed |
| DIS-44 r2 surface | CLI/MCP/docs/fixtures/evidence | `tmp/reviews/dis-44/surface-round-2.json` | 3/5 | changes requested | 2 | Child rollup P1 and docs P2; fixed |
| DIS-44 r3 code | protocol/convergence/storage/privacy | `tmp/reviews/dis-44/code-round-3.json` | 5/5 | clean | 0 | All six prior findings fixed; full/live gates passed |
| DIS-45 r1 code | protocol/storage/lifecycle/concurrency | `tmp/reviews/dis-45/code-round-1.json` | 3/5 | changes requested | 3 | Three P2s fixed before final gate |
| DIS-45 r2 code | protocol/storage/lifecycle/concurrency | `tmp/reviews/dis-45/code-round-2.json` | 5/5 | clean | 0 | Prior findings verified fixed |
| DIS-45 r1 surface | CLI/MCP/docs/authority/fixtures | `tmp/reviews/dis-45/surface-round-1.json` | 5/5 | clean | 0 | Derived surfaces and guidance aligned |
| DIS-35 r1 code | protocol/storage/privacy/freshness | `tmp/reviews/dis-35/code-round-1.json` | 3/5 | changes requested | 2 | Two P2s fixed |
| DIS-35 r2 code | protocol/storage/privacy/freshness | `tmp/reviews/dis-35/code-round-2.json` | 5/5 | clean | 0 | Prior findings verified fixed |
| DIS-39 r1 surface | contract/CLI/MCP/docs/freshness | `tmp/reviews/dis-39/surface-round-1.json` | 3/5 | changes requested | 2 | Two P2s fixed |
| DIS-39 r2 surface | contract/CLI/MCP/docs/freshness | `tmp/reviews/dis-39/surface-round-2.json` | 5/5 | clean | 0 | Prior findings verified fixed |
| DIS-18 r1 | backfill/runtime/surfaces | `tmp/reviews/dis-18/round-1.json` | 2/5 | changes requested | 6 | Three P1 and three P2; fixed |
| DIS-18 r2 | boundedness/durability/docs/evidence | `tmp/reviews/dis-18/round-2.json` | 2/5 | changes requested | 11 | Six P1 and five P2; fixed pending re-review |
| DIS-18 r3 | runtime/durability/scenario/docs/maintainability | `tmp/reviews/dis-18/round-3.json` | 2/5 | changes requested | 9 | Four P1 and five P2; fixed pending re-review |
| DIS-18 r4 | shared SQLite write ownership and evidence | report plus chat-only review | 2/5 | changes requested | 2 | One P1 and one P2; write boundary and evidence fixed |
| DIS-18 r5 | full prior-finding reconciliation | chat-only review | 2/5 | changes requested | 1 | One P1 dirty-read gap; all public registry access serialized |
| DIS-18 r6 | full prior-finding reconciliation | coordinator read-only review | 5/5 | clean | 0 | All round 1-5 findings verified fixed; no open P0-P2 |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |
| goal prompt checker | packet | passed | 3,752 characters; no unresolved placeholders |
| `just check` | DIS-42 | passed | 452 unit tests passed, 12 live tests deselected; build/package gate passed |
| targeted real App Server tests | DIS-42 | passed | approval, plan-mode user input, and MCP elicitation: 3 passed in 91.72s |
| `just scenario -- tests/scenarios/interactive_requests.toml` | DIS-42 | passed | Real permissive approval completed and created the expected file |
| App Server manifest comparison | DIS-42 | passed with explained label drift | Schema inventory identical; local binary reports `0.144.0-alpha.4` vs fixture `0.144.0` |
| focused canonical ingestion suite | DIS-44 | passed | 162 convergence, query/history, and registry tests |
| `just check` | DIS-44 | passed | 467 tests passed, 12 live tests deselected; build/package gate passed |
| `canonical_item_ingestion.toml` | DIS-44 | passed | Real Spark command item survived partial replay and was queryable locally |
| App Server manifest comparison | DIS-44 | passed with explained label drift | ThreadItem discriminants match generated 0.144 inventory |
| `dispatch schema query` / `query --help` | DIS-44 | passed | Derived output fields and CLI filters present; no hand-wired surface |
| `just check` | DIS-45 | passed | 486 tests passed, 13 live tests deselected; strict mypy/Ruff, package build and contents passed |
| targeted real App Server fork test | DIS-45 | passed | Persisted fork retained `forkedFromId`, no parent id, and was absent from parent/ancestor results |
| App Server manifest comparison | DIS-45 | passed with explained label drift | Structural inventory and topology fields match; local binary reports alpha version label |
| `just check` | DIS-35 | passed | 497 tests passed, 14 live tests deselected; strict mypy/Ruff, package build and contents passed |
| real App Server account/capacity probe | DIS-35 | passed | Redacted JSON from account, multi-bucket limits, reset credits, and usage without a model turn |
| `just check` | DIS-39 | passed | 503 tests passed, 14 live tests deselected; strict mypy/Ruff, package build and contents passed |
| isolated CLI/MCP usage smokes | DIS-39 | passed | Derived schema, database-only CLI, grouped MCP routing, and live redacted CLI refresh passed |
| `just check` | DIS-18 | passed | 560 tests passed, 15 live tests deselected; strict mypy/Ruff, package build and contents passed |
| targeted real App Server history integration | DIS-18 | passed | One persisted low-effort turn backfilled through the installed alpha's exact-turn fallback |
| `bounded_history_sync.toml` | DIS-18 | passed | Persisted unmanaged Codex thread proved raw-id registration, sync-attributed indexing, public bounds, observation, and stable re-sync |
| focused backfill/sync/surface review suite | DIS-18 | passed | 285 tests passed after the final review repair |
| `dispatch schema sync` / `sync --help` / package inspection | DIS-18 | passed | Derived bounds are present; wheel contains backfill and the updated skill |

## Prompt / Goal Alignment

- Checked by: coordinator.
- Result: aligned.
- Missing from prompt: none after review.
- Fixes made: replaced generic verification language with concrete test,
  integration, scenario, schema, and full-gate commands.

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| Linear realtime voice document | created | Future spike, outside this execution goal |
| DIS-41 | Todo | Parent adoption issue |
| DIS-42 | Todo / Urgent | First implementation milestone |
| DIS-44, DIS-45, DIS-35, DIS-39 | Todo / High | Fast-track milestones |
| DIS-18, DIS-46, DIS-47 | Todo | Next milestones |
| PR #73 | merged | `cd6879d`; unmanaged-thread pickup baseline |
| PR #74 | merged | `13bd959`; App Server 0.144 compatibility baseline |
| PR #75 | draft / checks running | Goal packet, now based on `main` |
| DIS-41 | In Progress | Parent adoption execution started |
| DIS-42 | In Progress | Interactive request milestone active |
| PR #75 | merged | Goal packet; `2f1c19a` |
| PR #76 | merged | DIS-42 interactive requests; `d388dd7` |
| DIS-42 | Done | Acceptance, live scenarios, and 5/5 review completed |
| DIS-44 | In Progress | Canonical item implementation and local review active |
| PR #77 | merged | DIS-44 canonical items; `bce4243b23cdd5139ecc8cf1a3300776a7189233` |
| DIS-44 | Done | Acceptance, live scenario, and 5/5 review completed |
| DIS-45 | In Progress | Provider topology implementation and local verification active |
| PR #78 | draft / green before retro amendment | DIS-45 provider topology; no open review threads |
| PR #78 | merged | DIS-45 provider topology; `d1aa3428ac9aa5f6be15fc176e033dfc6e2f7559` |
| DIS-45 | Done | Topology acceptance, live proof, and 5/5 reviews complete |
| DIS-35 | In Progress | Codex account/capacity substrate implementation active |
| DIS-39 | In Progress | Usage surface queued above DIS-35 |
| PR #79 / DIS-35 | merged / Done | Capacity substrate; `12da706`; checks and review clean |
| PR #80 / DIS-39 | merged / Done | Derived usage surface; `dd0f85e`; checks and review clean |
| DIS-18 | In Progress | Local implementation and 5/5 review complete; submission pending |

## Follow-Ups

- `DIS-43` durable policy engine after request-completeness behavior is proven.
- Realtime voice live spike from the Linear design document.

## Final State

- Completion proof: pending.
- Prompt length: pending.
- Review report summary: pending.
- Verification summary: pending.
- Forbidden actions audit: pending.
- Remaining P3s / risks: pending.
- Final transcript proof: pending.
