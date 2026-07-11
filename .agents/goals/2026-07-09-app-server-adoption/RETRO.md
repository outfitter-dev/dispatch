# Execution Retro: App Server Adoption

Date started: 2026-07-09
Date finalized: pending
Status: Active - DIS-35 implementation
Spec: `.agents/goals/2026-07-09-app-server-adoption/SPEC.md`
Goal: `.agents/goals/2026-07-09-app-server-adoption/GOAL.md`
Prompt: `.agents/goals/2026-07-09-app-server-adoption/PROMPT.md`
Refs: `.agents/goals/2026-07-09-app-server-adoption/REFS.md`

## Summary

- Objective: land the clear App Server 0.144 adoption work.
- Completion horizon: merged, tracker-reconciled, dogfooded, clean `main`.
- Authority used: Linear planning plus a dedicated Graphite packet commit.
- Outcome: preparation, DIS-42, DIS-44, and DIS-45 are merged; DIS-35 is in implementation.
- Tracker/PR/source-control state: PRs #73-#78 merged; DIS-35 is on its dedicated
  Graphite branch above clean `main`.
- Verification: prompt gate passed at 3,752 characters with no placeholders.
- Review state: DIS-42 round 1 findings fixed; code and surface round 2 are both
  5/5 clean with zero open P0-P2.
- Remaining risks: canonical item schema drift, partial provider history views,
  live scenario entitlement/capacity, and remaining milestone stack order.

## Readiness

- Prompt checked: passed at 3,752 characters with no unresolved placeholders.
- Goal/prompt alignment checked: passed manually after the final prompt gate.
- Review blockers: none for preparation.
- Verification blockers: none known.
- Tracker blockers: none; issues and Linear adoption plan exist.
- Authority blockers: release/publish intentionally excluded.
- Next action: complete `DIS-42` exploration and implementation.

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
| DIS-44 r3 surface | CLI/MCP/docs/fixtures/evidence | `tmp/reviews/dis-44/surface-round-3.json` | 5/5 | clean | 0 | All prior findings fixed; derived surfaces clean |
| DIS-45 r1 code | protocol/storage/lifecycle/concurrency | `tmp/reviews/dis-45/code-round-1.json` | 3/5 | changes requested | 3 | Three P2s fixed before final gate |
| DIS-45 r2 code | protocol/storage/lifecycle/concurrency | `tmp/reviews/dis-45/code-round-2.json` | 5/5 | clean | 0 | Prior findings verified fixed |
| DIS-45 r1 surface | CLI/MCP/docs/authority/fixtures | `tmp/reviews/dis-45/surface-round-1.json` | 5/5 | clean | 0 | Derived surfaces and guidance aligned |
| DIS-35 r1 code | protocol/storage/privacy/freshness | `tmp/reviews/dis-35/code-round-1.json` | 3/5 | changes requested | 2 | Two P2s fixed |
| DIS-35 r2 code | protocol/storage/privacy/freshness | `tmp/reviews/dis-35/code-round-2.json` | 5/5 | clean | 0 | Prior findings verified fixed |

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
