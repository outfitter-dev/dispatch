# Execution Retro: Dispatch Back On Track

Date started: 2026-08-18
Date finalized: pending
Status: In progress
Plan: `.agents/plans/2026-08-18-dispatch-back-on-track/PLAN.md`
Goal: `.agents/plans/2026-08-18-dispatch-back-on-track/GOAL.md`

## Execution Summary

- Objective: restore reviewed Studio/Codex/Claude work to trunk, prove this
  machine, and leave the remaining Claude path dependency-ordered.
- Final outcome: in progress; all four slices are review-clean at their recorded
  tips, and merge/readiness/live-provider gates remain closed.
- Final branch / stack tip: PR #94 head `142c58a`; PR #95 head `f51e95e`; draft
  PR #96 head `8659ee2`.
- Final PR range: [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) through
  [PR #96](https://github.com/outfitter-dev/dispatch/pull/96), not merged.
- Final tracker state: DIS-64 Ready to Merge; DIS-65 and DIS-57 In Review at the
  recorded audit; downstream dependency chain remains blocked.
- Final verification state: all four local/full/hosted gates are green at the
  recorded tips; PR #95 exact-tip review is 5/5 with no findings.
- Remaining risks / P3s: live App Server post-merge proof and live Claude proof pending.
- Archive state: not ready.

## Branch / PR / Issue Ledger

| Order | Issue | Branch | PR | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | [DIS-65](https://linear.app/outfitter/issue/DIS-65) | `codex/recover-studio-bridge-20260818` | [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) | Merge-ready, approval gated | 5/5 local review; CI green |
| 2 | [DIS-64](https://linear.app/outfitter/issue/DIS-64) | `dis-64-enforce-a-minimum-supported-codex-build-instead-of-an-unenforced-exact` | [PR #94](https://github.com/outfitter-dev/dispatch/pull/94) | Merge-ready after PR #93, approval gated | 5/5 combined review; CI green |
| independent | [DIS-57](https://linear.app/outfitter/issue/DIS-57) | `dis-57-launch-claude-background-sessions-through-the-supported-cli` | [PR #95](https://github.com/outfitter-dev/dispatch/pull/95) | Merge-ready, approval gated | 5/5 review; hosted gates green at `f51e95e` |
| independent | [DIS-66](https://linear.app/outfitter/issue/DIS-66) | `dis-66-remediate-open-dependabot-runtime-alerts-without-taking-mcp` | [PR #96](https://github.com/outfitter-dev/dispatch/pull/96) | Draft, readiness/merge approval gated | 5/5; local/full/hosted gates green |
| coordination | none | `codex/dispatch-back-on-track-goal` | none | In progress | Durable packet only |

## Planning Discoveries

| Discovery | Evidence | Decision | Impact |
| --- | --- | --- | --- |
| Compatibility refresh and floor are intentionally stacked | PR #94 base is PR #93 branch | Merge PR #93 before PR #94 | Avoids losing provenance/floor behavior |
| A launched Claude session may outlive a timed-out launcher | local 3/5 review of PR #95 | Treat timeout/output failure after process start as indeterminate | Blocks merge until fixed/re-reviewed |
| cmux, Herdr, and zmx were absent at audit | local tool inventory | Installation and live evaluations need a separate authorization ceremony | DIS-62/DIS-63 remain next decision gates |
| Existing zmx baseline lacks safe receipt/transaction behavior | Claude control research and Linear chain | Do not begin DIS-54 implementation early | Prevents unsafe transport work |
| Studio/local ignored continuation state already matches and is preserved | recovery checksum inventory | Exclude it from cleanup and mirroring | Protects ongoing local context |
| `main` has nine shipped runtime dependency alerts | GitHub Dependabot audit | Remediate in one lock-only slice while keeping `mcp` on 1.x | Created DIS-66 and draft PR #96; does not block PRs #93-#95 |

## Deferred / Follow-Up Discoveries

| Issue | Discovery | Why Out Of Goal | Link |
| --- | --- | --- | --- |
| DIS-62 | Herdr must be evaluated with a disposable session | Requires install/live-provider authorization | [DIS-62](https://linear.app/outfitter/issue/DIS-62) |
| DIS-63 | cmux must be evaluated with a disposable session | Requires install/live-provider authorization | [DIS-63](https://linear.app/outfitter/issue/DIS-63) |
| DIS-54 | zmx evidence is unsafe for provider writes | Blocked by host/receipt chain | [DIS-54](https://linear.app/outfitter/issue/DIS-54) |
| DIS-50 | Public provider slice would be premature | Blocked by DIS-57, DIS-61, DIS-54 | [DIS-50](https://linear.app/outfitter/issue/DIS-50) |

## Tracker Mutations

| Time | Tracker Item | Mutation | Evidence |
| --- | --- | --- | --- |
| 2026-08-18 | DIS-11, DIS-12, DIS-15, DIS-16, DIS-17 | Reconciled completed work to Done | Live Linear audit |
| 2026-08-18 | DIS-13 | Kept Backlog because only documentation landed | Live Linear audit |
| 2026-08-18 | DIS-50 | Removed completed blockers; retained DIS-61, DIS-57, DIS-54 | [DIS-50](https://linear.app/outfitter/issue/DIS-50) |
| 2026-08-18 | GitHub issues 16, 17, 25, 26 | Added evidence-based scope comments; left open | [#16](https://github.com/outfitter-dev/dispatch/issues/16), [#17](https://github.com/outfitter-dev/dispatch/issues/17), [#25](https://github.com/outfitter-dev/dispatch/issues/25), [#26](https://github.com/outfitter-dev/dispatch/issues/26) |
| 2026-08-18 | [DIS-66](https://linear.app/outfitter/issue/DIS-66) | Created High-priority security issue, attached draft PR, moved to In Review | [PR #96](https://github.com/outfitter-dev/dispatch/pull/96) |

## Execution Log

```text
2026-08-18 - Studio recovery and compatibility slices
- Changed: recovered seven tracked Studio files without overwriting local ignored state; opened PRs #93 and #94.
- Verified: exact checksums/modes; just check; package gates; hosted CI/review.
- Result: both compatibility slices clean and independently reviewed 5/5.
- Next: explicit merge gate after all PR review debt is closed.
- Blockers: merge authorization.

2026-08-18 - Claude internal launch slice
- Changed: added internal direct-exec launch envelope, roster reconciliation, bounded output, pending semantics, and fake-runtime coverage in PR #95.
- Verified: focused tests, exact just check (722 passed, 17 deselected), package gates, hosted CI, prior Cursor threads resolved.
- Result: fresh local review scored 3/5 with one P1 on side-effect ambiguity after launcher timeout/output failure.
- Next: Sol Low implements typed indeterminate outcome and focused tests; then fresh re-review.
- Blockers: P1 review debt; live proof separately authorization gated.

2026-08-18 - Claude indeterminate-outcome repair rounds
- Changed: at `6cc6ca`, timeout/output-limit after process start preserves only bounded normalized short-ID candidates, reconciles exactly one, and otherwise raises non-retryable content-free indeterminate error; at `f51e95e`, exit-0 zero/multiple-ID output receives the same indeterminate treatment.
- Verified: first fix 31 focused tests and 727/17 full gate; second fix 33 focused tests and 729/17 full gate; worktree clean and remote synchronized.
- Result: both concrete P1 prompts implemented; exact-tip re-review scored 5/5;
  hosted CI/CodeQL/Graphite are green with zero unresolved threads.
- Next: request explicit merge approval separately from live-Claude authorization.
- Blockers: merge approval; no live Claude action authorized.

2026-08-18 - Durable goal packet
- Changed: wrote executable plan, goal prompt, references, and retro on `codex/dispatch-back-on-track-goal`.
- Verified: packet records branch order, dependency map, validation ladder, stop rules, and approval boundaries.
- Result: committed/pushed at `6e52c26`; fresh packet audit scored 5/5 with no findings and confirmed standalone execution without chat history.
- Next: ask for merge and live-provider approvals as separate ceremonies.
- Blockers: explicit merge approval; live-provider authorization is a later gate.

2026-08-18 - Runtime dependency security slice
- Changed: created DIS-66; updated only uv.lock for mcp 1.28.1, pydantic-settings 2.14.2, python-multipart 0.0.31, starlette 1.3.1, and cryptography 50.0.0; opened draft PR #96 at `8659ee2`.
- Verified: unchanged 55-package set; all nine fixed ranges satisfied; locked sync, CLI smokes, 20 focused tests, exact just check (696 passed, 17 deselected), package build/content, local review 5/5, hosted CI/CodeQL green.
- Result: clean independent security slice; no alert is reachable through the current stdio-only MCP imports, but all are release debt until merge closes them.
- Next: receive readiness/merge approval, land, then confirm all nine alerts close before completing DIS-66.
- Blockers: explicit readiness and merge approval.
```

## Local Review Log

| Round | Scope / Lanes | Report Paths | P0/P1/P2 Result | Fix Commits / Notes |
| --- | --- | --- | --- | --- |
| 1 | PR #93 provenance/schema plus PR #94 version/package behavior | agent report, 2026-08-18 | clean, 5/5 | 71 focused tests; diff check; CI/review clean |
| 1 | PR #95 launch side effects, identity, privacy, bounds | agent report, 2026-08-18 | one P1, 3/5 | fix assigned; no live Claude invocation |
| 2 | PR #95 at `6cc6ca`, indeterminate timeout/output fix | agent report, 2026-08-18 | one P1, 3/5 | exit-0 zero/multiple-ID outcome still ordinary; fixed at `f51e95e` |
| 3 | PR #95 at `f51e95e` | agent report, 2026-08-18 | clean, 5/5 | 33 focused tests; `git diff --check`; both prior P1s closed |
| packet | Goal/plan/references/retro at `6e52c26` | agent report, 2026-08-18 | clean, 5/5 | tracked/pushed; exact integration and approval gates; live PR state consistent |
| security | PR #96 lock graph, markers, fixed ranges | agent report, 2026-08-18 | clean, 5/5 | unchanged 55-package set; exactly five version changes; no findings |
| packet security delta | Goal packet at `7d0a7d9` | agent report, 2026-08-18 | clean, 5/5 | 3,906-character goal; PR #96 draft gate and nine-alert closure are consistent across all files |

## Verification Log

| Check | Scope | Result | Evidence / Notes |
| --- | --- | --- | --- |
| `just check` | PR #93 | pass | 697 passed, 17 deselected; package gate pass |
| `just check` | PR #94 | pass | 715 passed, 17 deselected; package gate pass |
| focused review tests | PR #93 + PR #94 | pass | 71 passed in 1.35s; `git diff --check` pass |
| `just check` | PR #95 pre-P1 fix | pass | 722 passed, 17 deselected; package gate pass |
| `just check` | PR #95 first P1 fix `6cc6ca` | pass | 727 passed, 17 deselected; package gate pass |
| `just check` | PR #95 candidate `f51e95e` | pass | 729 passed, 17 deselected; package gate pass |
| `git diff --cached --check` | coordination packet before `6e52c26` | pass | four tracked files, no whitespace errors |
| `uv sync --locked` + CLI help smokes | PR #96 | pass | exact five patched package versions installed |
| focused MCP routing/derive/parity tests | PR #96 | pass | 20 passed |
| `just check` | PR #96 | pass | 696 passed, 17 deselected; build/package-content pass |
| `just test-int` | merged main | pending | run after merge; temporary CODEX_HOME and ephemeral lanes; record any auth/tool-dependent skips |
| live Claude launch | DIS-57 | approval gated | settings metadata/hash snapshot and disposable cleanup required |

## Remote Review / CI Log

| Time | PR | CI State | Review State | Scores / Signals | Unresolved P0/P1/P2 | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-18 | [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) | green | zero threads | local 5/5 | none | await merge approval |
| 2026-08-18 | [PR #94](https://github.com/outfitter-dev/dispatch/pull/94) | green | two prior Cursor threads fixed/replied/resolved | local stack 5/5 | none | await PR #93 then merge approval |
| 2026-08-18 | [PR #95](https://github.com/outfitter-dev/dispatch/pull/95) | green at `f51e95e` | zero unresolved threads | local round 3: 5/5; CI/CodeQL/Graphite green | none | await merge approval |
| 2026-08-18 | [PR #96](https://github.com/outfitter-dev/dispatch/pull/96) | CI/CodeQL green at `8659ee2` | zero unresolved threads at audit | local 5/5 | none | keep draft; await readiness/merge approval |

All three PRs were already non-draft before this packet began. This packet did
not perform or authorize a readiness transition.

## Review Feedback Resolutions

| Source | Score / Signal | Severity | Finding | Prompt To Fix | Resolution | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Local Terra review | 5/5 | none | PRs #93/#94 correctly scoped, packaged, tested, and ordered | none | accepted | 71 focused tests; clean worktrees; hosted gates green |
| Local Terra review round 1 | 3/5 | P1 | launcher timeout/output failure can leave a created session untracked and invite duplicate retry | preserve bounded partial short ID and reconcile, otherwise raise typed indeterminate launch error; add fake-runtime tests | fixed | `6cc6cafa278e58f448a9c54010272feead03a3dd`; 31 focused tests |
| Local Terra review round 2 | 3/5 | P1 | exit-0 output with zero/multiple IDs remained retryable-looking after a possible session creation | wrap post-start parse ambiguity as content-free non-retryable indeterminate; add zero/multiple regressions | fixed and verified in round 3 | `f51e95e0e16bdb82316270c7625701ef4a98d48f`; 33 focused tests |
| Local Terra review round 3 | 5/5 | none | both post-start ambiguity paths are typed, content-free, and non-retryable; known-ID roster parsing remains fail-closed | none | accepted | `f51e95e`; 33 focused tests; diff check clean |
| Local Terra security review | 5/5 | none | exact five package versions, unchanged 55-package set, valid marker refinement, all alert floors met | none | accepted | `8659ee2`; lock check and full gate green |

## Forbidden Actions Audit

| Action / Constraint | Status | Evidence |
| --- | --- | --- |
| No merge without explicit user approval | respected | PRs #93-#96 remain open |
| No new PR readiness transition without explicit user approval | respected | all three PRs inherited non-draft status before this packet |
| Keep new security PR draft until explicit readiness approval | respected | PR #96 remains draft |
| No package publish / registry mutation unless authorized | respected | build/package inspection only |
| No live Claude/provider mutation without separate approval | respected | no live Claude launch ran |
| No secrets, prompt content, or settings contents exposed | respected | metadata-only plan; fake-runtime tests |
| Preserve Studio/local ignored state | respected | checksum-matched state retained; no cleanup |
| Source-control writes after packet start stay scoped | respected | main agent owns packet; existing Sol Low owner is limited to DIS-57 P1 worktree |
| No unrelated destructive changes | respected | isolated worktrees and narrow diffs |

Prior to this packet, implementation subagents committed and pushed the recovered
PR slices under the earlier coordination instructions. That history is recorded
rather than retroactively represented as compliance with the packet's narrower
source-control ownership rule.

## Final State

- Goal completion condition: not yet met.
- Graphite / branch state: compatibility stack ordered PR #93 -> PR #94; PR #95 independent.
- PR state: PRs #93-#95 open/non-draft/merge-clean/review-clean; PR #96 open,
  draft, merge-clean, and locally/hosted-check clean.
- Source-control host lag: none known at recorded heads; re-read before mutation.
- Tracker state: partially reconciled; final post-merge update pending.
- Local review state: PRs #93/#94 5/5; PR #95 round 3 is 5/5; PR #96 is
  5/5; coordination packet audit is 5/5; no findings remain.
- Remote review state: PRs #93-#95 green at recorded heads; PR #96 CI/CodeQL
  green at `8659ee2`; zero unresolved threads at audit.
- Remote review scores: local scores recorded; hosted bot summaries recorded above.
- Verification: branch checks pass; merged-main/integration proof pending.
- Skipped checks: `just test-int` pending merge; opt-in `just scenario` excluded;
  live Claude proof pending authorization.
- Remaining P3s / risks: provider CLI behavior may drift; settings mutation risk must be measured.
- Follow-up issues created: none in this packet; existing DIS-62/DIS-63 are sufficient.
- Forbidden actions confirmation: current audit is clean.
- Packet archive readiness: not ready.
- Final transcript proof: pending.
