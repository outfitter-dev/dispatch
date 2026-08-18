# Execution Retro: Dispatch Back On Track

Date started: 2026-08-18
Date finalized: pending
Status: In progress
Plan: `.agents/plans/2026-08-18-dispatch-back-on-track/PLAN.md`
Goal: `.agents/plans/2026-08-18-dispatch-back-on-track/GOAL.md`

## Execution Summary

- Objective: restore reviewed Studio/Codex/Claude work to trunk, prove this
  machine, and leave the remaining Claude path dependency-ordered.
- Final outcome: resumed after explicit user approval of both the readiness/merge
  ceremony and the contained live-provider/host-evaluation ceremony; all four
  delivery PRs are merged and the authorized live work is complete.
- Final branch / stack tip: `main` at `ea4b731`; the coordination packet remains
  isolated on `codex/dispatch-back-on-track-goal`.
- Final PR range: [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) through
  [PR #96](https://github.com/outfitter-dev/dispatch/pull/96), all merged.
- Final tracker state: DIS-57, DIS-62, DIS-63, DIS-64, and DIS-65 Done; DIS-61
  moved to Todo with its prerequisite evaluations complete; DIS-66 remains In
  Review because GitHub still reports nine open alerts.
- Final verification state: merged-main build, package, CLI, exact `just check`,
  real isolated App Server integration, contained Claude launch, and both host
  evaluations are complete.
- Remaining risks / P3s: GitHub Dependabot alert re-evaluation remains pending;
  the dependency graph already reports the patched versions.
- Archive state: not ready.

## Branch / PR / Issue Ledger

| Order | Issue | Branch | PR | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | [DIS-65](https://linear.app/outfitter/issue/DIS-65) | `codex/recover-studio-bridge-20260818` | [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) | Merged at `e63b538` | 5/5 local review; CI green |
| 2 | [DIS-64](https://linear.app/outfitter/issue/DIS-64) | `dis-64-enforce-a-minimum-supported-codex-build-instead-of-an-unenforced-exact` | [PR #94](https://github.com/outfitter-dev/dispatch/pull/94) | Restacked and merged at `260f77c` | 5/5 combined review; rerun CI/CodeQL/Graphite green |
| independent | [DIS-57](https://linear.app/outfitter/issue/DIS-57) | `dis-57-launch-claude-background-sessions-through-the-supported-cli` | [PR #95](https://github.com/outfitter-dev/dispatch/pull/95) | Merged at `1b6fc3f`; live proof passed | 5/5 review; one isolated session reconciled and removed |
| independent | [DIS-66](https://linear.app/outfitter/issue/DIS-66) | `dis-66-remediate-open-dependabot-runtime-alerts-without-taking-mcp` | [PR #96](https://github.com/outfitter-dev/dispatch/pull/96) | Merged at `ea4b731`; alert indexing pending | 5/5; local/full/hosted gates green |
| evaluation | [DIS-62](https://linear.app/outfitter/issue/DIS-62) | none | none | Done | Herdr 0.8.0 optional for observation/manual attach; reject automated input |
| evaluation | [DIS-63](https://linear.app/outfitter/issue/DIS-63) | none | none | Done | cmux 0.64.22 optional operator host; reject external automated input |
| next | [DIS-61](https://linear.app/outfitter/issue/DIS-61) | none | none | Todo | Evaluation blockers cleared; capability-driven fail-closed contract next |
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
| Herdr's direct-controller lease does not cover API input | Live 0.8.0 controller/API race | Do not use Herdr for automated writes; observation/manual attach only | DIS-62 completed with optional/reject guidance |
| cmux safe-default socket rejects an external Dispatch process | Live 0.64.22 ancestry denial and official access-mode docs | Do not enable `allowAll`; treat an in-cmux broker as future design work | DIS-63 completed with optional/reject guidance |
| Claude credentials are expired on this machine | Attached live frontend rejected the synthetic turn | Preserve transport evidence but do not claim provider completion | No credential mutation; future model-turn proof needs re-authentication |
| GitHub's dependency graph sees patched versions while alerts remain open | SBOM reports mcp 1.28.1, pydantic-settings 2.14.2, python-multipart 0.0.31, starlette 1.3.1, cryptography 50.0.0 | Keep DIS-66 In Review; do not manually dismiss alerts | External scanner reconciliation remains the only completion gate |

## Deferred / Follow-Up Discoveries

| Issue | Discovery | Why Out Of Goal | Link |
| --- | --- | --- | --- |
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
| 2026-08-18 | [DIS-65](https://linear.app/outfitter/issue/DIS-65) | Marked Done after merged compatibility evidence | [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) |
| 2026-08-18 | [DIS-66](https://linear.app/outfitter/issue/DIS-66) | Corrected merge auto-close back to In Review while nine alerts remain open | Live Dependabot API and current dependency-graph SBOM |
| 2026-08-18 | [DIS-57](https://linear.app/outfitter/issue/DIS-57) | Added contained live launch/reconciliation/cleanup evidence; kept Done | [PR #95](https://github.com/outfitter-dev/dispatch/pull/95) |
| 2026-08-18 | [DIS-62](https://linear.app/outfitter/issue/DIS-62) and [DIS-63](https://linear.app/outfitter/issue/DIS-63) | Recorded live evaluation evidence and optional/reject decisions; marked Done | Herdr 0.8.0 and cmux 0.64.22 |
| 2026-08-18 | [DIS-61](https://linear.app/outfitter/issue/DIS-61) | Recorded host decisions and moved from Backlog to Todo | Both evaluation blockers complete |

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

2026-08-18 - Pre-merge isolated App Server integration
- Changed: no source changes; ran the exact real integration suite on the compatibility stack tip and the patched dependency tip, then recorded the evidence on both PRs.
- Verified: PR #94 at `142c58a` passed 17/17 in 231.65s; PR #96 at `8659ee2` passed 17/17 in 236.94s. Both harnesses used temporary isolated CODEX_HOME state and ephemeral lanes; daemon and lifecycle end-to-end cases passed.
- Result: no auth/tool skips and no live user-thread pollution; opt-in `just scenario` remained excluded.
- Next: repeat `just test-int` on merged `main` after the approved merge ceremony.
- Blockers: explicit readiness/merge approval.

2026-08-18 - Authorization-gate pause
- Changed: no code or remote state; refreshed `origin/main`, all four PRs, review threads, and all five owned worktrees after the same approval boundary persisted for three consecutive goal turns.
- Verified: local `main` and `origin/main` remain `276413e`; PRs #93-#96 are open and merge-clean at the recorded heads; PRs #93-#95 remain non-draft, PR #96 remains draft; hosted checks are green; unresolved review threads are 0/0/0/0; every owned worktree is clean and synchronized with its remote.
- Result: the goal cannot lawfully progress to landing or live-provider proof without direct user approval under repository policy.
- Next: on resume, re-read live state, then perform only the ceremony or ceremonies explicitly approved by the user.
- Blockers: explicit readiness/merge authorization; separately, explicit live-Claude/provider authorization.

2026-08-18 - Both ceremonies authorized
- Changed: user explicitly replied “Approve both” and delegated ordering; no readiness or merge mutation occurred before this ledger update.
- Verified: re-read repo guidance and Graphite/GitHub/Linear/goal workflow instructions; live main, PR heads, hosted checks, review threads, and owned worktrees remain clean.
- Result: authorization gates are open. Merge ceremony will run first so live Claude proof exercises landed code.
- Next: restore missing local Graphite metadata for the PR #93 -> PR #94 stack, dry-run it, mark draft PR #96 ready, and revalidate all newly triggered gates before merge.
- Blockers: none; stop on any topology, CI, review, or live-state divergence.

2026-08-18 - Merge topology and readiness checkpoint
- Changed: restored Graphite tracking metadata for `main` -> PR #93 -> PR #94, synchronized the two existing PRs to Graphite without code rewrites, and marked PR #96 ready under the user's explicit approval.
- Verified: `gt submit --stack --update-only --no-edit --dry-run` proposed only “Sync to Graphite” and “New parent”; actual submit updated the existing PRs; `gt merge --dry-run` reports the stack ready in bottom-up order. PR #96 remains at `8659ee2`, Graphite mergeability is green, and Cursor readiness review is in progress.
- Result: stack topology is now authoritative in both local Graphite metadata and hosted PR state; no merge has run yet.
- Next: wait for PR #96 Cursor review, resolve any actionable findings, re-read all four PR heads/checks/threads, update this retro if needed, then execute the authorized merge.
- Blockers: PR #96 Cursor readiness review must complete cleanly.

2026-08-18 - Final pre-merge gate
- Changed: no code; waited for PR #96 readiness-only reviews and performed the final four-PR head/check/thread read plus a fresh Graphite merge dry run.
- Verified: PR #96 Cursor/CI/CodeQL/Graphite all pass at `8659ee2`; all four PRs have zero unresolved threads and unchanged heads. PR #94's Graphite check is intentionally `in_progress` with the explicit message that it will pass when downstack PR #93 merges and that Graphite must merge the stack; GitHub CI is green and `gt merge --dry-run` reports the stack ready.
- Result: no P0/P1/P2, CI, topology, or mergeability blocker remains. The only non-green signal is Graphite's expected downstack ordering sentinel, which the authorized `gt merge` operation is designed to satisfy.
- Next: run `gt merge --no-interactive` from the PR #94 stack tip, verify both hosted PRs merged in order, then merge independent PRs #95 and #96 only after refreshing their live gates against the new main.
- Blockers: none.

2026-08-18 - Compatibility stack merged; Claude launch checkpoint
- Changed: executed the authorized Graphite stack merge. PR #93 merged first at `e63b538`; Graphite retargeted/restacked PR #94 from `142c58a` to `682a849`, reran CI/CodeQL/Graphite successfully, and merged it at `260f77c`. Synchronized existing PR #95 to Graphite metadata without code changes.
- Verified: live `origin/main` is `260f77c`; PRs #93 and #94 report merged in the correct order. PR #95 remains at reviewed head `f51e95e`, GitHub reports CLEAN, all hosted checks are green, unresolved threads are zero, and a fresh `gt merge --dry-run` reports its one-branch stack ready.
- Result: recovered Studio compatibility and minimum-version work are on trunk with no duplicated stack diff. The independent Claude launch primitive is ready for its authorized merge.
- Next: run Graphite merge for PR #95, verify the new main, then independently refresh and merge PR #96.
- Blockers: none.

2026-08-18 - Claude launch merged; dependency security checkpoint
- Changed: merged PR #95 through Graphite at `1b6fc3f`; tracked and synchronized existing PR #96 to Graphite metadata without changing its code.
- Verified: PR #95 reports merged at reviewed head `f51e95e`; live `origin/main` is `1b6fc3f`. PR #96 remains at `8659ee2`, GitHub reports CLEAN, all CI/CodeQL/Cursor/Graphite checks are green, unresolved threads are zero, and `gt merge --dry-run` reports its one-branch stack ready.
- Result: the internal Claude launch primitive is on trunk; the independent five-package security remediation is at its final authorized merge gate.
- Next: merge PR #96 through Graphite, verify all four hosted PRs and new main, then begin post-merge local/runtime/alert proof.
- Blockers: none.

2026-08-18 - All four PRs merged and trunk verified
- Changed: merged PR #96 through Graphite at `ea4b731`; fast-forwarded the clean primary checkout from `276413e` to live `origin/main` at `ea4b731`; synchronized the locked environment to the five patched dependency versions.
- Verified: all four PRs report MERGED with merge commits `e63b538`, `260f77c`, `1b6fc3f`, and `ea4b731`. On merged main, both CLI help smokes passed; exact `just check` passed Ruff/format/strict mypy, 748 tests with 17 deselected, sdist/wheel build, and package-content validation; `just test-int` passed 17/17 with 748 deselected in 256.02s, including daemon and lifecycle end-to-end cases with no skips.
- Result: landed code, local build/package/CLI, and isolated runtime-readiness are proven on the actual merged tree. The Dependabot API still reports nine open alerts immediately after merge despite every locked version meeting the fix floors, so DIS-66 remains open pending authoritative re-indexing.
- Next: poll Dependabot to closure, reconcile DIS-65/DIS-64 immediately, keep DIS-57 pending its authorized live Claude proof, and keep DIS-66 pending API closure.
- Blockers: GitHub Dependabot indexing lag only for DIS-66 completion.

2026-08-18 - Contained Claude launch proof
- Changed: no source; launched one synthetic background session through the merged internal primitive under a disposable workspace and isolated `CLAUDE_CONFIG_DIR`.
- Verified: the short ID reconciled to exactly one full provider UUID; the isolated roster had one matching background row; exact `claude stop` plus `claude rm` returned it to zero; normal user settings fingerprints were unchanged; no matching process or temp artifact remained.
- Result: DIS-57's supported launch/reconciliation/cleanup contract is proven on Claude Code 2.1.228 and the issue carries the live evidence.
- Next: evaluate the two terminal-host candidates without treating host transport as provider receipt.
- Blockers: none for DIS-57; later authenticated provider-turn tests require a renewed Claude login.

2026-08-18 - Herdr 0.8.0 evaluation
- Changed: installed the official Homebrew formula; created one exact named session and disposable workspaces; attached a Herdr frontend to a supervisor-owned Claude background UUID; exercised reads, process inspection, literal text, named keys, direct control, controller contention, restart/recovery, and cleanup.
- Verified: wrong-pane input failed before bytes; no canary appeared in retained Herdr logs/metadata; one provider row remained through attach and frontend reconstruction; a second direct controller failed without takeover; server restart restored layout and left the provider owner intact; exact session/workspace/process cleanup passed.
- Result: optional for observation/manual attachment, rejected for Dispatch automated input. Screen/input changes did not advance the pane revision, `pane.send_input` has no expected revision, and API prompt input succeeded while a direct controller held the terminal.
- Next: encode these unavailable capabilities explicitly in DIS-61 rather than weakening the input contract.
- Blockers: the attached synthetic turn reached the intended frontend but Claude rejected model execution because the account login is expired; no provider completion is claimed.

2026-08-18 - cmux 0.64.22 evaluation
- Changed: installed the official Homebrew cask; launched the app and two empty synthetic terminal workspaces; inspected the installed 0.64.22 CLI surface and tested safe-default socket access from Dispatch.
- Verified: stable UUID/ref targeting plus screen, input, event, process, health, move, restore, and cleanup commands are present. The external Dispatch process was rejected by the default ancestry ACL. No `allowAll` override was enabled, no Claude payload was sent, and the app, two synthetic restore workspaces, stale socket markers, and disposable tree were removed.
- Result: optional as an operator terminal/observation host, rejected for the current external Dispatch input path. The safe default excludes the daemon and the API exposes no revision-bound atomic text-plus-Enter admission.
- Next: DIS-61 may consider a narrowly authenticated mode or an explicit in-cmux broker, but must remain fail-closed by default.
- Blockers: none for the evaluation decision; authenticated end-to-end Claude execution remains unavailable until re-login.

2026-08-18 - Tracker and dependency-graph reconciliation
- Changed: marked DIS-65, DIS-62, and DIS-63 Done; preserved DIS-57/DIS-64 Done; moved DIS-61 to Todo; corrected DIS-66 from merge-triggered Done back to In Review.
- Verified: GitHub's dependency-graph SBOM already reports all five patched versions from merged main, but the Dependabot alert endpoint still returns alerts 1-9 open and no dismissed/auto-dismissed alerts.
- Result: code, PRs, and Linear now tell the truth; DIS-66 intentionally remains open until GitHub's alert evaluator catches up.
- Next: continue bounded polling; never substitute manual dismissal for a fixed-state result.
- Blockers: GitHub Dependabot alert-state reconciliation only.

2026-08-18 - Final synthetic-state cleanup audit
- Changed: deleted one leftover isolated Claude evaluation tree and the transient cmux screenshot after a final name/process inventory found that macOS `mktemp -t` had placed the Claude tree under `/var/folders/.../T`, outside the original cleanup trap's `/tmp` allowlist.
- Verified: no Claude process referenced the exact tree; no evaluation Claude roster row, Herdr named session, cmux process, synthetic cmux restore workspace, or matching temporary path remains. All owned Git worktrees are clean.
- Result: the missed temporary artifact was corrected transparently; the tree contained only synthetic isolated session state and was not recoverable after exact deletion.
- Next: retain the broader macOS temporary-path allowlist in future live ceremony cleanup.
- Blockers: none.

2026-08-18 - Dependabot supported refresh checkpoint
- Changed: after the successful default-branch dependency-graph run had been complete for more than one hour, invoked GitHub's documented `Refresh Dependabot alerts` action exactly once from the authenticated repository alert menu; no alert was dismissed and no security setting changed.
- Verified: the Dependabot page advanced from a one-hour-old graph build to `Dependency files checked Aug 18, 2026` for commit `ea4b731`, confirming the background manifest rebuild processed the merged default branch. The live SBOM still reports all five patched versions. A fresh Terra packet audit at coordination commit `ae3e081` scored 5/5 with no findings.
- Result: GitHub accepted and processed the supported rebuild, but ten minutes later all nine alert records still reported `state: open`, `fixed_at: null`, and their original June-August `updated_at` timestamps. The exact support packet is recorded in `REFS.md` and on DIS-66, which remains In Review.
- Next: with explicit user authorization, submit the prepared GitHub Support escalation. Do not dismiss alerts, refresh again inside the one-hour rate limit, or manufacture a dependency change.
- Blockers: GitHub Dependabot alert-state reconciliation only.
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
| `just test-int` | PR #94 at `142c58a` | pass | 17 passed, 715 deselected in 231.65s; isolated App Server, daemon, and lifecycle coverage |
| `just test-int` | PR #96 at `8659ee2` | pass | 17 passed, 696 deselected in 236.94s; patched dependency graph under isolated App Server coverage |
| `uv sync --locked` + CLI help smokes | merged `main` at `ea4b731` | pass | exact five patched dependency versions installed; both entry points load |
| `just check` | merged `main` at `ea4b731` | pass | Ruff/format/mypy; 748 passed, 17 deselected; build/package-content pass |
| `just test-int` | merged `main` at `ea4b731` | pass | 17 passed, 748 deselected in 256.02s; no skips |
| Dependabot dependency-graph SBOM | merged `main` at `ea4b731` | pass | all five packages report the patched versions from `uv.lock` |
| Dependabot open-alert query | merged `main` at `ea4b731` | pending | alerts 1-9 remain open; no dismissed or auto-dismissed alerts; DIS-66 remains In Review |
| live Claude launch | DIS-57 | pass | one reconciled full UUID; isolated roster 1 -> 0; exact stop/remove; normal settings unchanged; no temp/process residue |
| Herdr 0.8.0 host evaluation | DIS-62 | decision complete | attach/restart/cleanup pass; reject automated input because no revision-bound admission and API bypasses controller lease |
| cmux 0.64.22 host evaluation | DIS-63 | decision complete | rich API confirmed; safe-default external access denied; no `allowAll`; reject current external automated input path |

## Remote Review / CI Log

| Time | PR | CI State | Review State | Scores / Signals | Unresolved P0/P1/P2 | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-18 | [PR #93](https://github.com/outfitter-dev/dispatch/pull/93) | green | zero threads | local 5/5 | none | merged at `e63b538` |
| 2026-08-18 | [PR #94](https://github.com/outfitter-dev/dispatch/pull/94) | green | two prior Cursor threads fixed/replied/resolved; integration proof [comment](https://github.com/outfitter-dev/dispatch/pull/94#issuecomment-5331466658) | local stack 5/5 | none | merged at `260f77c` |
| 2026-08-18 | [PR #95](https://github.com/outfitter-dev/dispatch/pull/95) | green at `f51e95e` | zero unresolved threads | local round 3: 5/5; CI/CodeQL/Graphite green | none | merged at `1b6fc3f` |
| 2026-08-18 | [PR #96](https://github.com/outfitter-dev/dispatch/pull/96) | CI/CodeQL green at `8659ee2` | zero unresolved threads; integration proof [comment](https://github.com/outfitter-dev/dispatch/pull/96#issuecomment-5331466800) | local 5/5 | none | merged at `ea4b731`; alert state pending |

PRs #93-#95 were already non-draft before this packet began. PR #96 moved from
draft to ready only after explicit user approval, then all four were merged.

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
| No merge without explicit user approval | satisfied | user explicitly approved both ceremonies; PRs #93-#96 merged only afterward |
| No new PR readiness transition without explicit user approval | exercised within approval | PR #96 marked ready only after user explicitly approved both ceremonies |
| Keep new security PR draft until explicit readiness approval | satisfied | PR #96 stayed draft until approval, then moved to ready |
| No package publish / registry mutation unless authorized | respected | build/package inspection only |
| No live Claude/provider mutation without separate approval | satisfied | contained live work ran only after explicit approval |
| No secrets, prompt content, or settings contents exposed | respected | synthetic prompts only; metadata/hash comparisons; no settings contents retained |
| Preserve Studio/local ignored state | respected | checksum-matched state retained; no cleanup |
| Source-control writes after packet start stay scoped | respected | main agent owns packet; existing Sol Low owner is limited to DIS-57 P1 worktree |
| No unrelated destructive changes | respected | isolated worktrees and narrow diffs; only empty synthetic cmux restore state and stale socket markers were deleted during exact cleanup |

Prior to this packet, implementation subagents committed and pushed the recovered
PR slices under the earlier coordination instructions. That history is recorded
rather than retroactively represented as compliance with the packet's narrower
source-control ownership rule.

## Final State

- Goal completion condition: met except for the external Dependabot alert-state
  reconciliation explicitly required before DIS-66 can close.
- Graphite / branch state: compatibility stack ordered PR #93 -> PR #94; PR #95 independent.
- PR state: PRs #93-#96 merged in the authorized order with live merge commits
  recorded above.
- Source-control host lag: none known at recorded heads; re-read before mutation.
- Tracker state: reconciled; only DIS-66 remains In Review because the live alert
  endpoint has not acknowledged the patched graph.
- Local review state: PRs #93/#94 5/5; PR #95 round 3 is 5/5; PR #96 is
  5/5; coordination packet audit is 5/5; no findings remain.
- Remote review state: all four PRs merged from green reviewed heads with zero
  unresolved threads at the final pre-merge audit.
- Remote review scores: local scores recorded; hosted bot summaries recorded above.
- Verification: merged-main full gate, real integration, contained Claude launch,
  Herdr evaluation, and cmux evaluation pass at their stated scopes; only
  Dependabot alert closure remains pending.
- Skipped checks: opt-in `just scenario` remained excluded; authenticated Claude
  model completion was unavailable because the account login is expired.
- Remaining P3s / risks: provider CLI behavior may drift; normal Claude runtime
  metadata changes when the default profile is used; neither evaluated host
  satisfies revision-bound serialized input.
- Follow-up issues created: none in this packet; existing DIS-62/DIS-63 are sufficient.
- Forbidden actions confirmation: current audit is clean.
- Packet archive readiness: wait only for Dependabot alert closure and DIS-66 Done.
- Final transcript proof: report the merged-main gates, live ceremony, host
  decisions, exact cleanup, and the one authoritative external blocker.
