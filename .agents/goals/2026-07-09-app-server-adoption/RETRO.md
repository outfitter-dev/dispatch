# Execution Retro: App Server Adoption

Date started: 2026-07-09
Date finalized: pending
Status: Active - full-stack review repairs
Spec: `.agents/goals/2026-07-09-app-server-adoption/SPEC.md`
Goal: `.agents/goals/2026-07-09-app-server-adoption/GOAL.md`
Prompt: `.agents/goals/2026-07-09-app-server-adoption/PROMPT.md`
Refs: `.agents/goals/2026-07-09-app-server-adoption/REFS.md`

## Summary

- Objective: land the clear App Server 0.144 adoption work.
- Completion horizon: merged, tracker-reconciled, dogfooded, clean `main`.
- Authority used: Linear planning plus a dedicated Graphite packet commit.
- Outcome: preparation, DIS-42, DIS-44, DIS-45, DIS-35, DIS-39, DIS-18, DIS-46,
  and DIS-47 are merged. Goal-wide review found cross-milestone repairs now in progress.
- Tracker/PR/source-control state: PRs #73-#84 merged; DIS-41 and every scoped child
  are Done; `main` is synchronized and clean at `00f7f32`. Release/publish work remained
  outside this goal.
- Verification: prompt gate passed at 3,752 characters with no placeholders.
- Review state: milestone reviews are clean. Full-stack round one found five P1 and four
  P2 findings across cumulative behavior and closeout evidence; repairs are active.
- Remaining risks: unresolved full-stack findings and hosted review threads block closeout.

## Readiness

- Prompt checked: passed at 3,752 characters with no unresolved placeholders.
- Goal/prompt alignment checked: passed manually after the final prompt gate.
- Review blockers: full-stack round one findings must be fixed and independently re-reviewed.
- Verification blockers: none known.
- Tracker blockers: merged issue states are reconciled, but the goal closeout remains active.
- Authority blockers: release/publish intentionally excluded.
- Next action: finish full-stack repairs, reconcile hosted threads, rerun the full gate and
  targeted live proof, merge this closeout branch, then verify clean `main`.

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-09 | Initial execution contract | User requested an ambitious direct-start goal | Matt |
| 2026-07-11 | Supersede one combined live scenario with the milestone scenario/integration matrix | A single scenario would duplicate costly interactive, history, capacity, permission, topology, and image turns while reducing failure isolation. Equivalent acceptance is proven by the three checked-in milestone scenarios plus targeted isolated integration tests recorded below. | Execution coordinator under delegated autonomy |

The superseding matrix covers the promised combined scenario behavior without one brittle fixture:

| Capability | Isolated proof |
| --- | --- |
| interactive approvals, user input, MCP elicitation | `interactive_requests.toml` plus three targeted real request-manager integrations |
| canonical item ingestion and query | `canonical_item_ingestion.toml` |
| bounded resume/history backfill | `bounded_history_sync.toml` plus targeted paging/fallback integration |
| topology | targeted persisted fork/parent/ancestor integration |
| account/capacity and permission profiles | targeted no-turn App Server integrations |
| rich local/HTTPS inputs | authored `open` + `send` integration through production handlers |

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

2026-07-11 - DIS-46 permission profiles and preset integration
- Added typed, cursor-guarded `permissionProfile/list` support and a cwd-scoped
  durable catalog with source, allowed state, and freshness. The generated
  manifest now distinguishes stable profile discovery from the experimental
  `permissions` thread/turn field and normalizes alpha version labels.
- Added one authored `permissions` read op deriving top-level CLI schema/help
  and grouped daemon-read MCP behavior. `new`, packets, global/repo defaults,
  named presets, fork, follow-up sends, queue delivery, and daemon resume share
  one persisted `permission_profile` setting without storing provider secrets.
- Precedence is symmetric: a later named profile clears inherited granular
  sandbox/approval settings, while later granular settings clear an inherited
  profile. Same-layer conflicts remain invalid. Persisted profiles are
  cwd-revalidated before resume; unavailable, disallowed, or older unsupported
  providers fail closed and leave the lane visibly `error`.
- Review round 1 found two unique P1s and two P2s across the code/surface pair:
  restart revalidation, layered precedence, filtered freshness, and missing
  mechanism distinction. Round 2 verified every repair and returned 5/5 clean
  on both sides with no open P0-P2.
- Verification: `just check` passed with 575 tests and 16 live tests deselected;
  strict mypy/Ruff and package gates passed. The isolated App Server test listed
  and applied `:read-only` without a turn. An isolated public daemon/CLI smoke
  returned all three current profiles, and schema/help/MCP parity stayed derived.
- Next: commit/submit DIS-46, reconcile hosted checks and Linear, merge through
  Graphite, then continue DIS-47.
- Blockers: none.

2026-07-11 - DIS-47 rich image inputs
- Added one discriminated text/image/local-image contract shared by `new` and `send`.
  CLI ergonomics derive repeatable `--image`/`--image-url` plus stdin text while MCP
  retains the compact structured `content` array. App Server manifest guards now record
  the shared turn/start and turn/steer image union and detail values.
- Local PNG/JPEG/GIF/WebP files are signature-checked, bounded, hashed off the event loop,
  and never copied into the registry. HTTPS references are persisted as durable references,
  resolved only to public addresses, connected through a DNS-pinned TLS transport without
  redirects or environment proxies, fetched under a shared deadline, and converted to
  ephemeral data URLs because live 0.144 rejects ordinary remote URLs.
- Schema v21 adds structured queue references and metadata. Delivery revalidates files,
  remote content, and model modalities; immediate failures surface to the caller, restart
  recovery preserves v20 text queues, and capture strips inline image bytes and secrets.
- Dry-run now runs the same side-effect-free file/model preflight before workspace mutation
  and reports safe image references. CLI schema describes the actual shell adapter while
  grouped MCP schemas inline authored unions without dangling definitions.
- Verification: `just check` passed with 603 tests and 17 live tests deselected, strict
  mypy/Ruff, package build, and package contents. An isolated authored Dispatch path used
  `open` plus `send` with a local red PNG and a remote Python logo; the agent returned
  `RED PYTHON` in 15.15 seconds.
- Review round one: code/data 2/5 with six P1 and two P2; surface 2/5 with three P1 and
  four P2. Subsequent rounds verified all implementation repairs; surface round three is
  5/5 clean and code round five is 5/5 clean after final evidence reconciliation.
- Next: commit/submit/merge DIS-47, update Linear, then perform the goal-wide final review
  and clean-main dogfood pass.
- Blockers: none.

2026-07-11 - DIS-47 merge and goal-wide review
- PR #84 passed CI, CodeQL, and Graphite mergeability, then merged as `00f7f32`.
  Graphite synchronized to a clean `main` and removed the merged DIS-47 branch.
- Linear DIS-47 and parent DIS-41 are Done with final evidence comments. DIS-34 remains
  explicit separate provider-neutral inventory scope rather than an unrecorded omission.
- Clean-main dogfood reran real isolated account/capacity, permission-profile, and authored
  local/remote image paths: three integration tests passed in 12.07 seconds. Derived
  `send`, `query`, and `permissions` schemas passed jq assertions.
- The adoption-goal agent performed no release or publish action. Independent PR #81 merged
  the 0.8.2 version bump during this execution window; PyPI publishing was outside this goal
  and was not performed or verified here.
- Full-stack round one subsequently found cumulative topology, capacity, sync, and permission
  defects plus hosted-thread/evidence drift. The goal returned to active for repair.
- Blockers: full-stack findings and hosted review reconciliation.

2026-07-11 - Full-stack round-one repairs
- Fixed topology refresh to observe active and archived provider pages separately, including
  cached lifecycle transitions in both directions.
- Deduplicated full capacity reads by provider-limit/window identity and made id-less pushes
  conservatively reuse a named or sole existing limit while preserving independent freshness.
- Made explicit `sync --full` bypass unchanged/incremental shortcuts and rescan from byte zero.
- Kept `permissions` on the generic derived CLI path while resolving its cwd in the caller,
  and moved conflicting `new` permission settings onto the authored input validator so CLI,
  control socket, and MCP retain the validation taxonomy.
- Reconciled the combined-scenario contract through the formal amendment/matrix above and
  corrected the release audit to distinguish independent PR #81 from goal-agent actions.
- Verification: `just check` passed 613 tests with 17 opt-in tests deselected, strict
  mypy/Ruff, package builds, and package contents. Targeted account/capacity, permission,
  and topology App Server integrations passed 3 tests in 8.35 seconds.
- Next: submit this repair/closeout PR, reply to and resolve all six hosted review threads,
  obtain clean full-stack re-reviews, merge, and verify clean `main`.
- Blockers: hosted review reconciliation and full-stack re-review.
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
| DIS-46 r1 code | protocol/config/storage/restart | `tmp/reviews/dis-46/code-round-1.json` | 3/5 | changes requested | 2 | One P1 and one P2; fixed |
| DIS-46 r1 surface | precedence/surfaces/docs | `tmp/reviews/dis-46/surface-round-1.json` | 3/5 | changes requested | 3 | Two P1 and one P2; fixed |
| DIS-46 r2 code | protocol/config/storage/restart | `tmp/reviews/dis-46/code-round-2.json` | 5/5 | clean | 0 | Prior findings verified fixed; 265 focused tests |
| DIS-46 r2 surface | precedence/surfaces/docs | `tmp/reviews/dis-46/surface-round-2.json` | 5/5 | clean | 0 | All prior findings fixed; no new P0-P2 |
| DIS-47 r1 code | rich input/runtime/queue/security | `tmp/reviews/dis47-code/round-1.json` | 2/5 | changes requested | 8 | Six P1 and two P2; fixes applied pending re-review |
| DIS-47 r1 surface | CLI/MCP/docs/evidence | `tmp/reviews/dis47-surface/round-1.json` | 2/5 | changes requested | 7 | Three P1 and four P2; fixes applied pending re-review |
| DIS-47 r2 code | rich input/runtime/queue/security | `tmp/reviews/dis47-code/round-2.json` | 4/5 | changes requested | 1 | Original findings fixed; transient missing-import snapshot repaired |
| DIS-47 r2 surface | CLI/MCP/docs/evidence | `tmp/reviews/dis47-surface/round-2.json` | 4/5 | changes requested | 1 | Private HTTPX integration replaced with public transport boundary |
| DIS-47 r3 code | full prior-finding reconciliation | `tmp/reviews/dis47-code/round-3.json` | 4/5 | changes requested | 1 | Code clean; stale retro evidence corrected |
| DIS-47 r3 surface | full prior-finding reconciliation | `tmp/reviews/dis47-surface/round-3.json` | 5/5 | clean | 0 | All prior findings verified fixed; no worthwhile P3 remains |
| DIS-47 r4 code | durable evidence reconciliation | `tmp/reviews/dis47-code/round-4.json` | 4/5 | changes requested | 1 | Stale high-level retro prose corrected |
| DIS-47 r5 code | durable evidence reconciliation | `tmp/reviews/dis47-code/round-5.json` | 5/5 | clean | 0 | All prior findings fixed; exact evidence and handoff agree |
| full-stack r1 code | cumulative architecture/runtime/closeout | `tmp/reviews/full-stack-code/round-1.json` | 4/5 | changes requested | 1 | Implementation ready; completion evidence premature |
| full-stack r1 surface | cumulative product/surfaces/tracker | `tmp/reviews/full-stack-surface/round-1.json` | 2/5 | changes requested | 8 | Five P1 and three P2; repairs active |
| full-stack r2 code | cumulative repair reconciliation | `tmp/reviews/full-stack-code/round-2.json` | 5/5 | clean | 0 | All code/runtime findings fixed; 215 focused tests and live proof |
| full-stack r2 surface | cumulative surface/tracker reconciliation | `tmp/reviews/full-stack-surface/round-2.json` | 4/5 | changes requested | 1 | All defects fixed; only six hosted threads remain to reconcile after submit |

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
| `just check` | DIS-46 | passed | 575 tests passed, 16 live tests deselected; strict mypy/Ruff and package gates passed |
| targeted App Server permission profile test | DIS-46 | passed | Paginated stable catalog and experimental `permissions` thread start succeeded without a model turn |
| isolated public daemon/CLI smoke | DIS-46 | passed | `permissions --json` returned current cwd-aware allowed profiles; daemon stopped cleanly |
| derived schema/help/MCP and manifest | DIS-46 | passed | One authored op covers CLI/MCP; manifest guards stable list and experimental launch fields |
| `just check` | DIS-47 | passed | 603 tests passed, 17 live tests deselected; strict mypy/Ruff and package gates passed |
| authored local/remote image integration | DIS-47 | passed | Real isolated `open` + `send` delivered a local PNG and HTTPS reference through production handlers; response identified `RED PYTHON` in 15.15 seconds |
| CLI/MCP/manifest/schema v21 checks | DIS-47 | passed | CLI schema reflects adapters, MCP content is self-contained, App Server union is guarded, and v20 queues migrate/restart safely |
| clean-main isolated dogfood | full goal | passed | Account/capacity, permission profiles, and authored local/HTTPS image delivery: 3 passed in 12.07 seconds; derived schema jq assertions passed |
| `just check` | full-stack repairs | passed | 613 tests passed, 17 live tests deselected; strict mypy/Ruff and package gates passed |
| targeted post-repair App Server integrations | full-stack repairs | passed | Account/capacity, permission profiles, and persisted fork/topology: 3 passed in 8.35 seconds |
| full-stack code review round 2 | full goal | 5/5 | No unresolved P0-P2 findings on the closeout branch |
| hosted checks for PR #85 | full-stack repairs | passed | CI and all CodeQL jobs completed successfully |
| hosted review reconciliation | full goal | passed | Six carried P2 threads on PRs #78, #79, #82, and #83 were answered with PR #85 evidence and resolved; those PRs and #85 have zero unresolved current threads |

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
| PR #82 / DIS-18 | merged / Done | Bounded incremental history sync; `18b57cf`; checks and review clean |
| PR #83 / DIS-46 | merged / Done | Permission profiles and preset integration; checks and review clean |
| DIS-47 | In Progress | Rich image implementation and final local review active |
| PR #84 / DIS-47 | merged / Done | Rich image inputs; `00f7f32`; hosted checks and local reviews clean |
| DIS-41 | Done | App Server 0.144 fast-track parent closed after every scoped child merged |
| PR #85 | draft / green | Full-stack review repairs; hosted checks green and carried review threads reconciled |

## Follow-Ups

- `DIS-43` durable policy engine after request-completeness behavior is proven.
- Realtime voice live spike from the Linear design document.

## Final State

- Completion proof: pending final full-stack re-review and closeout merge. PRs #73-#84 are
  merged and DIS-41/scoped children are Done, but cumulative repairs remain on this branch.
- Prompt length: 3,752 characters; no unresolved placeholders.
- Review report summary: milestone reviews converged to 5/5. Full-stack round one reopened
  five P1 and four P2 findings; code round two is 5/5 and surface round two verified every
  repair except hosted-thread reconciliation, which is now complete and pending final re-review.
- Verification summary: latest `just check` passed 613 tests with 17 opt-in tests deselected,
  strict mypy/Ruff, wheel/sdist, and package contents; targeted post-repair integration passed.
- Forbidden actions audit: this goal agent performed no release/publish, secret mutation,
  destructive git, remote config, or out-of-scope tracker action. Independent PR #81 merged
  the 0.8.2 version bump during the execution window; package publishing was not part of this goal.
- Remaining P3s / risks: pending final full-stack review. Older App Server capability limits
  and DIS-34 remain explicit future scope.
- Final transcript proof: the authored rich-input live path returned `RED PYTHON`; final
  clean-main account/capacity, permissions, and rich-input dogfood passed 3 tests in 12.07s.
