# Execution Retro: Provider Capacity Completion

Date started: 2026-07-14
Date finalized: 2026-07-14
Status: complete at ready-PR horizon
Spec: `.agents/goals/2026-07-14-provider-capacity-completion/SPEC.md`
Goal: `.agents/goals/2026-07-14-provider-capacity-completion/GOAL.md`
Prompt: `.agents/goals/2026-07-14-provider-capacity-completion/PROMPT.md`
Refs: `.agents/goals/2026-07-14-provider-capacity-completion/REFS.md`

## Summary

- Objective: Complete DIS-34's remaining Claude provider account/capacity foundation.
- Completion horizon: `ready-pr`.
- Authority used: Packet preparation, scoped Linear mutation, Graphite branching/PR publication, implementation, tests, docs, and isolated read-only provider smokes.
- Outcome: DIS-36, DIS-37, and DIS-38 are implemented as a three-PR Graphite stack (#88, #89, #90), green, cleanly reviewed, and ready for review/stack merge.
- Tracker/PR/source-control state: DIS-34/DIS-36/DIS-37/DIS-38 In Progress; PRs #88/#89/#90 linked; DIS-40 remains blocked by DIS-37; current stacked branch `dis-38-store-normalized-provider-observations-for-mesh-heartbeats`.
- Verification: final full gate passed 673 tests with 17 intentional deselections plus sdist/wheel/package checks; focused final provider suite passed 135; both isolated provider smokes passed.
- Review state: every standing and targeted gate is clean at 5/5 with zero open P0-P2; remote Cursor passes are recorded on #88/#89, with #90's final pass required after the retro-only head update.
- Remaining risks: Claude statusline schema drift, manual wrapper setup ergonomics, and future mesh transport/node identity remain outside this ready-PR horizon.

## Readiness

- Prompt checked: yes; 3,857/4,000 characters with no unresolved placeholders.
- Goal/prompt alignment checked: yes; sequence, loop, review, checks, rules, stop rules, done/not-done, evidence, and persistence are carried directly.
- Review blockers: none; all local standing and targeted reviews are clean.
- Verification blockers: none known.
- Tracker blockers: none within the ready-PR horizon; issue statuses remain In Progress until merge.
- Authority blockers: merge/release/publish and live Claude config mutation are not authorized.
- Next action: merge the stack through Graphite when authorized, then close the Linear children and parent.

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-14 preparation | Sequence DIS-38 reconciliation after DIS-36/DIS-37 while allowing minimal model extensions in DIS-36 | Audit found most DIS-38 storage/surface work already shipped in PRs #79/#80 | Coordinator within existing scope |
| 2026-07-14 DIS-37 | Fingerprint the statusline session id instead of retaining the raw id | Correlation is preserved while the packet privacy boundary excludes raw session identifiers from disk and usage output | Coordinator within existing scope; divergence to be documented on DIS-37 |

## Execution Log

```text
2026-07-14 preparation - live state and provider seam audit
- Changed: Created the goal packet only.
- Verified: Clean main/no open PRs; provider model/store/surface seams; live Claude CLI output shapes without retaining raw identity; focused baseline 75 passed.
- Result: Direct-start milestone stack is executable.
- Next: Packet validation and DIS-36 red-green implementation.
- Blockers: None.
```

```text
2026-07-14 execution - DIS-36 Claude account/runtime tracer bullets
- Changed: Added bounded async Claude auth/agents probes, provider-neutral auth/runtime fields and freshness, independent usage refresh, privacy-safe docs, and 13 behavior tests.
- Verified: Focused provider/store suite 88 passed; surface suite 58 passed; just check 625 passed; wheel/sdist build passed; isolated live usage smoke returned Claude ready with aggregate state only.
- Result: Raw email, org id, cwd, agent/session ids, names, and source command payloads were mechanically absent from live output; temporary daemon/home cleaned up.
- Next: Review, commit, draft PR, and CI.
- Blockers: None.
```

```text
2026-07-14 execution - DIS-36 first-round review fixes
- Changed: Preserved independent capacity on auth failure/sign-out, bounded and normalized agent states/counts, added true subprocess byte limits and cancellation cleanup, refreshed providers concurrently with failure isolation, and surfaced the Claude CLI version.
- Verified: Focused provider/store suite 93 passed; surface suite 58 passed; just check 631 passed; wheel/sdist build passed.
- Result: All four standing-review and five privacy-review P1/P2 blockers are addressed locally with regression coverage.
- Next: Same-reviewer recheck, fix commit, draft PR, and CI.
- Blockers: Review recheck pending.
```

```text
2026-07-14 execution - DIS-36 second-round review fixes
- Changed: Kept successful account/runtime component timestamps and cached facts unchanged when later probes fail; corrected the documented subprocess inventory and exact surface-test evidence.
- Verified: Focused provider/store suite 93 passed; surface suite 58 passed; just check 631 passed; wheel/sdist build passed.
- Result: All round-two standing/privacy blockers are addressed locally with regression coverage for account and runtime freshness.
- Next: Same-reviewer round-three recheck, fix commit, draft PR, and CI.
- Blockers: Review recheck pending.
```

```text
2026-07-14 execution - DIS-36 remote review follow-up
- Changed: A successful signed-out observation now clears cached runtime, runtime freshness, and CLI version while preserving independent capacity and usage facts.
- Verified: Focused Claude provider tests passed; full gate rerun before resubmission.
- Result: Cursor Bugbot's stale-runtime finding is fixed with regression coverage.
- Next: Push parent update, resolve the review thread, and restack DIS-37.
- Blockers: None.
```

```text
2026-07-14 execution - DIS-37 statusline capacity capture
- Changed: Added an opt-in standalone stdin helper, bounded normalized snapshot schema, atomic owner-only writes beneath DISPATCH_HOME, decimal Claude windows, monotonic merge into provider observations, stale rendering, and manual non-clobbering wrapper docs.
- Verified: Focused statusline/provider/store suite 107 passed; surface suite 58 passed; just check 645 passed; wheel/sdist build passed; isolated capture-to-usage smoke returned both windows and excluded raw session/path/model identifiers.
- Result: Supported Claude.ai five-hour/seven-day capacity is available without editing Claude settings or calling the private OAuth endpoint; missing limits and older snapshots do not erase newer capacity.
- Next: Commit, standing and targeted atomicity/privacy reviews, draft PR, and CI.
- Blockers: Review pending.
```

```text
2026-07-14 execution - DIS-37 first-round review fixes
- Changed: Merged windows independently in both the snapshot file and provider observation, added per-window timestamps, serialized writers with a file lock, rejected older captures monotonically, and fsynced the containing directory after rename.
- Verified: Focused statusline/provider/store suite 112 passed; surface suite 58 passed; just check 650 passed; wheel/sdist build passed.
- Result: All standing and targeted round-one P1/P2 findings are addressed with both-direction partial-window, older-capture, and directory-fsync regression tests.
- Next: Same-reviewer recheck, fix commit, draft PR, and CI.
- Blockers: Review recheck pending.
```

```text
2026-07-14 execution - DIS-37 second-round review fix
- Changed: Added an explicit current `rate_limits_available` fact while retaining the last valid per-window values/timestamps when a newer statusline event has no rate-limit fields.
- Verified: Focused statusline/provider/store suite 113 passed; just check 651 passed; wheel/sdist build passed.
- Result: Current unavailability remains observable without destructive cache loss or fabricated window freshness.
- Next: Same-reviewer round-three recheck, fix commit, draft PR, and CI.
- Blockers: Review recheck pending.
```

```text
2026-07-14 execution - DIS-37 third-round review fix
- Changed: Provider refresh now imports retained snapshot windows even when the newest event says rate limits are currently unavailable, while keeping the partial/error signal separate.
- Verified: Focused statusline/provider/store suite 114 passed; surface suite 58 passed; just check 652 passed; wheel/sdist build passed.
- Result: A fresh registry receives the last valid timestamped windows plus explicit current unavailability; no cache loss or freshness fabrication remains.
- Next: Same-reviewer round-four recheck, fix commit, draft PR, and CI.
- Blockers: Review recheck pending.
```

```text
2026-07-14 execution - DIS-38 observation contract reconciliation
- Changed: Accepted ADR-0025, reconciled Linear to latest-value snapshots/read-time staleness, added deterministic contract tests, preserved Codex components across probe failures, and enforced bounded/privacy-safe observations before SQL persistence.
- Verified: Focused provider suite 133 passed; just check 671 passed with 17 deselected; wheel/sdist/package checks passed; invalid model-copy repro leaves the prior SQLite row unchanged.
- Result: Provider observations are bounded per provider/host/config, retain stale last-known components truthfully, reject raw identity/reset handles and malformed timestamps, and remain suitable for future heartbeat embedding without implementing transport.
- Next: Clean review rechecks, final remote review, and ready-PR handoff.
- Blockers: Merge and tracker completion remain outside authorized horizon.
```

```text
2026-07-14 execution - DIS-38 remote review follow-up
- Changed: Successful rate-limit responses now clear omitted reset-credit count/list together, while legacy pre-bound observations with oversized collections are normalized on read so existing registries remain usable.
- Verified: Focused provider suite 135 passed; just check 673 passed with 17 deselected; wheel/sdist/package checks passed; direct legacy 65-window row reads as the newest bounded 64.
- Result: Both Cursor findings on PR #90 are fixed with regression coverage and no manual registry repair requirement.
- Next: Push, resolve/reply to both review threads, request final Cursor pass, and mark the PR ready when green.
- Blockers: Final remote re-review pending.
```

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| preparation | Provider/repo seam audit | subagent response | not scored | complete | 0 | Read-only evidence used to refine sequence and privacy boundary. |
| preparation | Tracker/repo reconciliation audit | subagent response | not scored | complete | 0 | DIS-34 should move to In Progress; DIS-40 should depend on DIS-37; unrelated stale issues remain outside this goal. |
| 1 | Standing DIS-36 diff review | `tmp/reviews/standing/dis-36-round-1.json` | 3/5 | changes requested | 4 | Preserve cached capacity on auth failures; bound agent states; enforce output bytes; expose CLI version. |
| 1 | Claude privacy/resilience review | `tmp/reviews/privacy/dis-36-round-1.json` | 2/5 | changes requested | 5 | Also isolate provider refreshes concurrently and guarantee subprocess cleanup. |
| 2 | Standing DIS-36 diff review | `tmp/reviews/standing/dis-36-round-2.json` | 3/5 | changes requested | 3 | Prior findings closed; keep failed auth from refreshing stale account facts and correct docs/evidence counts. |
| 2 | Claude privacy/resilience review | `tmp/reviews/privacy/dis-36-round-2.json` | 3/5 | changes requested | 1 | Prior findings closed; retain the last valid runtime aggregate when roster refresh fails. |
| 3 | Standing DIS-36 diff review | `tmp/reviews/standing/dis-36-round-3.json` | 5/5 | clean | 0 | All standing findings closed; exact focused, surface, type, lint, format, and diff checks passed. |
| 3 | Claude privacy/resilience review | `tmp/reviews/privacy/dis-36-round-3.json` | 5/5 | clean | 0 | Component cache semantics and all earlier privacy/subprocess/isolation findings verified closed. |
| remote | Cursor Bugbot on PR #88 | GitHub review thread `discussion_r3582064305` | not scored | changes requested | 1 | Signed-out refresh retained stale runtime/version; fixed by clearing account-dependent runtime facts. |
| 1 | Standing DIS-37 incremental review | `tmp/reviews/standing/dis-37-round-1.json` | 3/5 | changes requested | 1 | Partial snapshots must replace by window key without erasing the independently absent window. |
| 1 | Statusline atomicity/privacy review | `tmp/reviews/statusline/dis-37-round-1.json` | 2/5 | changes requested | 3 | Also serialize/monotonically order captures and fsync the containing directory. |
| 2 | Standing DIS-37 incremental review | `tmp/reviews/standing/dis-37-round-2.json` | 5/5 | clean | 0 | Keyed merge, original per-window freshness, installed entrypoint, file mode, privacy, and checks verified. |
| 2 | Statusline atomicity/privacy review | `tmp/reviews/statusline/dis-37-round-2.json` | 3/5 | changes requested | 1 | Locking/durability/partial merge fixed; both-absent input still erased cached windows. |
| 3 | Standing DIS-37 incremental review | `tmp/reviews/standing/dis-37-round-3.json` | 3/5 | changes requested | 1 | File retained unavailable cache, but first registry refresh ignored those windows. |
| 3 | Statusline atomicity/privacy review | `tmp/reviews/statusline/dis-37-round-3.json` | 3/5 | changes requested | 1 | Same downstream gate lost retained windows on an empty registry. |
| 4 | Standing DIS-37 incremental review | `tmp/reviews/standing/dis-37-round-4.json` | 5/5 | clean | 0 | Empty-registry recovery, partial/unavailable state, timestamps, focused/surface/type/lint checks verified. |
| 4 | Statusline atomicity/privacy review | `tmp/reviews/statusline/dis-37-round-4.json` | 5/5 | clean | 0 | Exact two-window unavailable sequence, staleness, raw-id exclusion, locking/durability/bounds all verified. |
| 1 | Standing DIS-38 incremental review | `tmp/reviews/standing/dis-38-round-1.json` | 3/5 | changes requested | 2 | Preserve Codex components across subprobe failures and enforce provenance bounds. |
| 1 | Observation/privacy contract review | `tmp/reviews/observation/dis-38-round-1.json` | 3/5 | changes requested | 2 | Enforce durable identity/timestamp/size invariants and repair PR evidence rendering. |
| 2 | Standing DIS-38 incremental review | `tmp/reviews/standing/dis-38-round-2.json` | 3/5 | changes requested | 2 | Validate before commit and bound nested reset-credit values. |
| 2 | Observation/privacy contract review | `tmp/reviews/observation/dis-38-round-2.json` | 3/5 | changes requested | 2 | Same pre-write gap plus whitespace-only optional-field failure. |
| 3 | Standing DIS-38 incremental review | `tmp/reviews/standing/dis-38-round-3.json` | 5/5 | clean | 0 | Pre-SQL validation, prior-row preservation, component cache, window caps, and nested bounds verified. |
| 3 | Observation/privacy contract review | `tmp/reviews/observation/dis-38-round-3.json` | 5/5 | clean | 0 | Independent invalid-write repro, whitespace normalization, PR evidence, privacy, and focused suite verified. |
| remote | Cursor Bugbot on PR #90 | GitHub review threads `discussion_r3582374957` and `discussion_r3582374965` | not scored | changes requested | 2 | Reset-credit omission mismatch and legacy oversized-window read compatibility; both fixed with regression coverage. |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |
| `uv run pytest tests/core/test_capacity.py tests/registry/test_store.py -q` | Existing provider/store baseline | passed | 75 passed in 2.04s. |
| `check-goal-prompt --no-placeholders` | Packet prompt | passed | 3,857/4,000 characters; no placeholders. |
| `goal-loop-doctor` | Packet readiness | passed | Required files and sections present; no review reports yet. |
| `uv run pytest tests/core/test_claude_capacity.py tests/core/test_capacity.py tests/registry/test_store.py -q` | DIS-36 provider/store behavior | passed | 88 passed. |
| `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_parity.py -q` | Derived CLI/MCP parity | passed | 58 passed. |
| `just check` | Full repository and package gate | passed | Ruff, format, strict mypy, 625 tests, sdist/wheel, package contents. |
| isolated `dispatch usage --provider claude --json` | Live Claude CLI/manual privacy smoke | passed | Provider ready; aggregate roster only; sensitive source values absent; isolated homes cleaned up. |
| `uv run pytest tests/core/test_claude_capacity.py tests/core/test_capacity.py tests/registry/test_store.py -q` | Post-review provider/store behavior | passed | 93 passed. |
| `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_parity.py -q` | Post-review derived CLI/MCP parity | passed | 58 passed. |
| `just check` | Post-review full repository and package gate | passed | Ruff, format, strict mypy, 631 tests, sdist/wheel, package contents. |
| `uv run pytest tests/core/test_claude_statusline.py tests/core/test_claude_capacity.py tests/core/test_capacity.py tests/registry/test_store.py -q` | DIS-37 capture/merge/provider behavior | passed | 107 passed. |
| `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_parity.py -q` | DIS-37 derived CLI/MCP parity | passed | 58 passed. |
| `just check` | DIS-37 full repository and package gate | passed | Ruff, format, strict mypy, 645 tests, sdist/wheel, package contents. |
| isolated `dispatch-claude-statusline` to `dispatch usage --provider claude --json` | DIS-37 end-to-end privacy/freshness smoke | passed | Decimal five-hour/seven-day windows fresh; raw session, cwd, transcript, and model id absent; temporary daemon/home removed after plugin-clone cleanup race. |
| `uv run pytest tests/core/test_claude_statusline.py tests/core/test_claude_capacity.py tests/core/test_capacity.py tests/registry/test_store.py -q` | Post-review DIS-37 capture/merge/provider behavior | passed | 112 passed. |
| `just check` | Post-review DIS-37 full repository and package gate | passed | Ruff, format, strict mypy, 650 tests, sdist/wheel, package contents. |
| `uv run pytest tests/core/test_claude_statusline.py tests/core/test_claude_capacity.py tests/core/test_capacity.py tests/registry/test_store.py -q` | DIS-37 unavailable-with-cache behavior | passed | 113 passed. |
| `just check` | DIS-37 second-review full repository and package gate | passed | Ruff, format, strict mypy, 651 tests, sdist/wheel, package contents. |
| `uv run pytest tests/core/test_claude_statusline.py tests/core/test_claude_capacity.py tests/core/test_capacity.py tests/registry/test_store.py -q` | DIS-37 empty-registry unavailable-cache behavior | passed | 114 passed. |
| `just check` | DIS-37 third-review full repository and package gate | passed | Ruff, format, strict mypy, 652 tests, sdist/wheel, package contents. |
| `uv run pytest tests/core/test_provider_observation_contract.py tests/core/test_capacity.py tests/core/test_claude_capacity.py tests/core/test_claude_statusline.py tests/registry/test_store.py -q` | Final DIS-38 provider contract behavior | passed | 135 passed. |
| `just check` | Final DIS-38 full repository and package gate | passed | Ruff, format, strict mypy, 673 tests, 17 deselected, sdist/wheel, package contents. |
| GitHub Actions | PRs #88/#89/#90 | passed | Required `check` workflow green on each implementation head before final retro-only update. |

## Prompt / Goal Alignment

- Checked by: primary coordinator.
- Result: passed.
- Missing from prompt: none after correction.
- Fixes made: Split out Boundary, Evidence Contract, and Persistence; shortened prompt below 4,000 characters while retaining concrete checks and gates.

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| DIS-34 | In Progress | Assigned to Matt; Codex children done and Claude sequence active. |
| DIS-36 | In Progress | PR #88 ready for review; CI/CodeQL green; 5/5 standing and privacy review gates. |
| DIS-37 | In Progress | PR #89 ready; CI and Cursor green; 5/5 standing/statusline gates. |
| DIS-38 | In Progress | Assigned to Matt; PR #90 linked; accepted ADR and issue describe latest-value/read-time-staleness contract; 5/5 standing/observation gates. |
| DIS-40 | Backlog, blocked by DIS-37 | Explicitly deferred private endpoint spike until supported statusline evidence exists. |

## Follow-Ups

- Record unrelated stale Provider Event Log/History Index statuses separately; do not broaden this goal's tracker mutations.

## Final State

- Completion proof: Three scoped Graphite PRs exist with linked Linear issues, green local/full checks, green implementation CI, and clean standing plus targeted review reports.
- Prompt length: 3,857/4,000 characters.
- Review report summary: all final DIS-36 privacy, DIS-37 statusline, and DIS-38 observation reviews are clean at 5/5 with zero open P0-P2.
- Verification summary: 673 passed, 17 deselected; package artifacts built and checked; focused final suite 135 passed; isolated Claude account/statusline smokes passed.
- Forbidden actions audit: no secrets retained; no private Claude endpoints, live Claude settings mutation, merge, release, or publish performed.
- Remaining P3s / risks: supported statusline schema may drift; wrapper installation is manual; mesh transport/node identity and routing remain deferred.
- Final transcript proof: Linear DIS-36/DIS-37/DIS-38 and PRs #88/#89/#90 carry implementation, divergence, review, and verification evidence.
