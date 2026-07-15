# Execution Retro: Claude Control-Plane Research

Date started: 2026-07-15
Date finalized: 2026-07-15
Status: Executing baseline and supported-surface verification
Spec: `.agents/goals/2026-07-15-claude-control-plane-research/SPEC.md`
Goal: `.agents/goals/2026-07-15-claude-control-plane-research/GOAL.md`
Prompt: `.agents/goals/2026-07-15-claude-control-plane-research/PROMPT.md`
Refs: `.agents/goals/2026-07-15-claude-control-plane-research/REFS.md`

## Summary

- Objective: verify Claude session-control semantics and produce an implementation-ready Dispatch provider plan.
- Completion horizon: `ready-pr`.
- Authority: isolated low-cost research, scoped docs/spikes/ADRs/tracker, commit/push/draft/ready PR; no production implementation, merge, release, publish, live settings mutation, private endpoint, credential access, or existing-session messaging.
- Current state: delegated execution active on `docs/claude-control-plane-research`; baseline ledger complete and supported-surface audits in progress.
- Known baseline: Dispatch 0.10.0 observes Claude but controls only Codex; Claude Code 2.1.210 exposes several candidate direct surfaces; zmx 0.6.0 raw send is fire-and-forget.

## Readiness

- Prompt checked: passed at 3,994/4,000 characters with no unresolved placeholders.
- Goal/prompt alignment: passed preparation review; authority, boundary, sequence, reviews, evidence, stop rules, and completion horizon agree.
- Review blockers: none known before execution.
- Verification blockers: none known; live Claude model access must be confirmed by the delegate.
- Tracker blockers: none; DIS-9 and DIS-49 exist, while implementation issue decomposition is an execution deliverable.
- Authority blockers: global settings changes, existing-session mutation, tool installation/upgrades, private endpoints, production implementation, merge, release, and publish require separate approval.
- Next action: run packet checks, commit preparation, then delegate the raw `PROMPT.md` body as the first message to the research agent.

## Preparation Log

```text
2026-07-15 - Packet preparation
- Verified: clean synchronized main at Dispatch 0.10.0; no open PRs.
- Verified: all public new/send/steer/context/interject paths still terminate in the Codex App Server client protocol.
- Verified: Claude Code 2.1.210 advertises background agents, Agent View JSON discovery, durable/resumable session selectors, realtime stream JSON, hook events, remote control, named sessions, settings, permission modes, and worktrees.
- Verified: zmx 0.6.0 supplies persistent PTY sessions and raw input but explicitly provides no send completion marker or exit status.
- Tracker: DIS-9 is the existing hooks/receipts research issue; DIS-48 is usage capture only; DIS-49 specifies execution-provider shorthands but excludes the Claude transport.
- Decision: use a research/decision goal with a ready-PR horizon and require a hook-confirmed cross-process walking skeleton before implementation planning is accepted.
- Validation: `check-goal-prompt --no-placeholders` and `goal-loop-doctor` passed; `git diff --check` passed.
- Mutation audit: created only this goal packet branch and packet files; no Claude session/config/tracker/remote mutation.
```

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-15 | Initial research-to-decision contract | User requested exhaustive research before Claude implementation planning | Matt |

## Execution Log

```text
2026-07-15 - Delegated execution baseline
- Skill/callback: initialized as @Dispatch:ClaudeControlResearch with delegate-init; coordinator return ID is recorded in PROMPT.md.
- Repository: clean branch docs/claude-control-plane-research at 06ae5749e270358f86f4bff818d60f4ca976028c; no associated or open PR.
- Versions: dispatch 0.10.0; Claude Code 2.1.210; zmx 0.6.0.
- Live-state inventory (metadata only): 10 existing Agent View entries, 1 pre-existing zmx session, and 18 matching Claude processes. None were opened, messaged, interrupted, renamed, attached, or otherwise mutated.
- Settings guard: recorded SHA-256 and file metadata for user and repository-local Claude settings without printing contents. User settings hash starts 52a8b8fe; repository-local settings hash starts 378cd942; absent paths were recorded.
- Tracker: fetched DIS-9, DIS-48, and DIS-49 with relations. DIS-9 remains Todo and blocked by DIS-1/2/3; DIS-48 is adjacent usage capture; DIS-49 defines provider selection but explicitly excludes transport implementation.
- Source control: no mutation beyond this RETRO ledger update; no production implementation, release, publish, or existing-session action.
- Cleanup baseline: disposable prefix not yet allocated; zero research-created sessions/processes/temp repositories.

2026-07-15 - Stream JSON and resume milestone
- Allocated one temporary Git repository under `/tmp/dispatch-claude-control-<uuid>` with a per-invocation settings file and sanitized hook log. No live/project settings were modified.
- Created an explicit session UUID with print-mode stream JSON and a synthetic `DISPATCH-PROBE:m1` message. The result reported the chosen UUID and success; `UserPromptSubmit` captured the same UUID and marker.
- Resumed the same UUID from two fresh CLI processes with `m2` and `m3`. Both produced successful results; the corrected logger captured `SessionStart(source=resume)`, correlated `UserPromptSubmit(m3)`, `Stop`, and `SessionEnd` without prompt/model/transcript content.
- Surprise/failure: the first sanitizer used a jq `capture` expression that emitted no object when an event lacked a prompt, so only `UserPromptSubmit` was retained. This was a probe bug, not a Claude failure. Fixed the expression to emit null and reran; lifecycle events then appeared.
- Observed hook-event output includes multiple SessionStart/Stop hook executions because per-session hooks compose with existing lower-scope hooks. Only the Dispatch probe hook writes the sanitized probe log; implementation must dedupe normalized provider events and must not assume exclusive hook ownership.
- Interim verdict, later corrected by aggregate-hook probes: explicit Claude session UUID plus fresh-process `--resume` is a supported durable multi-turn composition. One Dispatch `UserPromptSubmit` observer is submission evidence only; one `Stop` is a repeatable cycle boundary, not completion alone.

2026-07-15 - Lifecycle, concurrency, attention, failure, and zmx milestone
- Interrupt/recovery: started a resumed turn that invoked a 30-second Bash sleep, observed correlated `UserPromptSubmit` and tool hooks, then sent SIGINT to the owned CLI process. It exited 130 without a new `Stop`; a fresh process resumed the same UUID and completed a later message. This matches the documented rule that user interruption does not emit `Stop`.
- Concurrent sends: two simultaneous `--resume <same-uuid> --print` processes both succeeded. Each `UserPromptSubmit` received a distinct provider `prompt_id`, and each `Stop` carried the matching `prompt_id`; submission order differed from launch order. Provider prompt IDs give exact cycle joins, but Dispatch must enforce a durable single-writer lease for ordered delivery.
- Duplicate send: two simultaneous processes carrying the same synthetic marker produced two distinct provider `prompt_id` values and two turns. Claude provides no idempotency key at this surface; ambiguous retries must reconcile receipts and never resend blindly.
- Hook failure: an added exit-1 `UserPromptSubmit` observability hook produced a hook response with exit code 1 while the Dispatch capture hook succeeded and the turn completed. A separate two-second hook with a one-second timeout was reported as `outcome=cancelled`, exit 1; the prompt still completed. Missing/failed/timed-out hook settlement means unknown processing, not rejection.
- Agent View attention: a named disposable `--bg` session returned short management ID `518b912b` and full UUID `518b912b-…`; its metadata reached `state=blocked`, `status=waiting`, `waitingFor=permission prompt`. Sanitized hooks joined `UserPromptSubmit`, `PermissionRequest(tool=AskUserQuestion)`, `Notification(type=permission_prompt)`, and `Stop` by one `prompt_id`.
- Agent View attach: `claude attach` accepted the synthetic answer, completed, detached with Ctrl-Z without stopping, then accepted a second attached prompt with a new `prompt_id` and matching `Stop`. The shell has lifecycle commands but no non-interactive reply command, so Agent View is a human supervision surface rather than the default Dispatch message transport.
- Agent View restart: a second disposable background session completed, was stopped (and disappeared from the default active JSON list), then `respawn` restored the same full UUID. Both background entries were stopped and removed; `--all`/cwd-filtered inventory returned zero matching rows.
- Fork: `--resume <uuid> --fork-session` completed under a new UUID while retaining the source UUID, confirming a real branch primitive with separate identity.
- zmx fake target: in isolated mode-0700/0600 directories, concurrent raw sends reached the synthetic target in `b` then `a` order despite `a` being launched first. A Ctrl-C raw send returned success but did not prevent `completed:c`. After killing the session, a send printed an unresponsive-session error yet exited 0. The isolated zmx inventory returned to zero.
- zmx security blocker: tagged 0.6.0 source hard-codes debug logging and logs PTY input bytes in recoverable hex. Even private 0700/0600 directories retain prompt bytes. Production Claude-over-zmx is a product/security decision and is excluded from the recommended first transport until logging can be disabled/redacted in a pinned supported build.

2026-07-15 - Aggregate receipts, transport/security review, and preflight milestone
- Transport review round 1 scored 2/5 with 3 P1 and 4 P2 findings. Security/product review round 1 scored 2/5 with 4 P1 and 2 P2 findings. Reports are local scratch under `tmp/reviews/transport/round-1.json` and `tmp/reviews/security-product/round-1.json`.
- Blocking sibling prompt hook: the Dispatch observer completed, a sibling `UserPromptSubmit` hook exited 2, no assistant activity or Stop occurred, yet the CLI emitted result subtype success. Therefore observer success and result success do not prove acceptance; processing requires aggregate prompt-hook settlement plus owned-stream assistant/tool activity.
- Continuing sibling Stop hook: one prompt produced two Stop occurrences with the same provider prompt ID, separated by further assistant activity. Completion requires the final Stop hook set to settle without continuation, terminal result success, and clean owned-process exit.
- Stream structure: `hook_started`/`hook_response` share CLI `hook_id`; raw hook payloads do not. Every ingest gets a daemon delivery ID, while repeated semantic Stop occurrences remain distinct.
- Retry correction: a retry is safe only when Dispatch proves no stdin frame write began. Any transport loss after a possible write is acceptance-indeterminate and blocks automatic drain.
- Interrupt correction: transport attempt state and provider completion state are separate. SIGINT after processing starts proves transport interruption only; provider completion remains unknown. Explicit operator abandonment may release Dispatch's queue while preserving that unknown fact.
- Trust correction: a per-generation nonce fences stale or misrouted Dispatch hook responses but is not spoof-resistant against same-UID hooks, repository code, or tools. Owned-stream corroboration, OS-user isolation, and Claude permission/sandbox policy are the boundary.
- Attach correction: Dispatch-owned `--resume`-for-send and human Agent View attach are verified separate capabilities. Writable registration of an unmanaged ordinary Claude UUID is unsupported in v1 because no content-free metadata validation primitive was proven.
- Privacy correction: removed durable prompt SHA-256 from the planned schema; the one-writer lease, session UUID, generation, and provider prompt ID correlate without adding a stable content fingerprint.
- No-message preflight: a disposable stream-input process with EOF emitted a successful Dispatch SessionStart hook response carrying the current nonce before any prompt frame. Sibling hook stdout was reduced immediately and not retained. This supports abort-before-write when managed settings suppress the hook channel.
- Process ownership fixture: `uv run python spikes/claude/process_group_probe.py` verified a POSIX `start_new_session` parent/child pgid and whole-group SIGINT/TERM cleanup. A live Claude descendant check remains an implementation scenario gate.
- Cleanup: review temp repo/settings removed, matching processes zero, settings hashes unchanged, and no existing session was touched.
```

## Experiment Ledger

| ID | Question | Method | Version/source | Result | Confidence | Artifact | Cleanup |
| --- | --- | --- | --- | --- | --- | --- | --- |
| prep-1 | Does Dispatch currently control Claude sessions? | Trace public handlers and client protocol | Dispatch 0.10.0 | No; control path is Codex-only | high | SPEC/REFS | n/a |
| prep-2 | Is zmx send a delivery receipt? | Inspect installed primary help | zmx 0.6.0 | No; raw input is fire-and-forget | high | REFS | n/a |
| base-1 | What supported candidate surfaces does the installed Claude CLI expose? | Bounded `claude --help` and `claude agents --help` inspection | Claude Code 2.1.210 | Background agents, Agent View JSON metadata, resume/continue/fork/session ID/name, stream JSON, hook-event output, remote control, per-session settings, permission modes, and worktrees are advertised | high | local help; research doc pending | read-only |
| base-2 | Can existing session inventory be bounded without transcript access? | `claude agents --json` reduced immediately to JSON type/count | Claude Code 2.1.210 | Yes; array shape with 10 entries. No entry bodies retained or printed | high | RETRO baseline | read-only |
| base-3 | What live-state guards exist before disposable experiments? | Count-only Agent View/zmx/process inventory plus settings file metadata and hashes | local baseline 2026-07-15 | Baseline captured; all research resources must use a unique prefix and return to zero | high | RETRO baseline | hashes unchanged; created inventories zero |
| life-1 | Can one durable UUID span independent processes and turns? | Explicit `--session-id`, then fresh `--resume` processes with synthetic markers | Claude Code 2.1.210 | Yes; one UUID across structurally completed turns | high | research doc + sanitized hooks | processes exited |
| life-2 | Can an interrupted turn resume? | Long tool turn, exact-process SIGINT, fresh resume | Claude Code 2.1.210 | Exit 130/no Stop; later same UUID completed | high | research doc | process exited |
| msg-1 | Are concurrent/duplicate sends ordered or idempotent? | Parallel resume processes with distinct and duplicate markers | Claude Code 2.1.210 | Both produced turns; order differed; duplicate got distinct prompt IDs | high | research doc | processes exited |
| hook-1 | Do hook failure/timeout reject a prompt? | Added exit-1 and timeout fixtures beside capture hook | Claude Code 2.1.210 | No; both fail open and turn completes | high | spike scripts + research doc | processes exited |
| attn-1 | Can attention and response be observed without transcript reads? | Disposable Agent View session asking a synthetic question | Claude Code 2.1.210 | JSON waiting state plus permission/notification hooks; attach response and Stop share prompt ID | high | research doc + fixture | stopped/removed |
| restart-1 | Does Agent View respawn preserve full identity? | Complete, stop, respawn, inspect metadata | Claude Code 2.1.210 | Same full UUID restored | high | RETRO/research doc | stopped/removed |
| zmx-1 | Does raw send prove delivery/order/interrupt? | Isolated fake REPL; parallel input, Ctrl-C, kill then send | zmx 0.6.0 | No; reordered, Ctrl-C unconfirmed, loss error exited zero | high | spike fake + research doc | killed; isolated count zero |
| hook-2 | Can one successful observer prove prompt acceptance? | Sibling capture + exit-2 prompt blocker | Claude Code 2.1.210 | No; observer and result success occurred without assistant/Stop | high | aggregate fixture + research doc | process exited |
| hook-3 | Does one Stop prove completion? | Sibling Stop hook continued exactly once | Claude Code 2.1.210 | No; two Stop cycles shared one prompt ID with assistant activity between | high | aggregate fixture + research doc | process exited |
| hook-4 | Can Dispatch prove hook presence before message write? | SessionStart nonce hook, stream input with immediate EOF | Claude Code 2.1.210 | Yes; owned hook response carried nonce before any user frame | high | preflight fixture + README | process exited; no prompt |
| proc-1 | Can a POSIX owner terminate an exact descendant group? | `start_new_session`, pid/pgid checks, SIGINT/TERM fake tree | local POSIX/Python | Yes; parent and child exited | high for primitive | executable spike | temp removed |

## Source / Version Ledger

| Source | Retrieved/version | Use | State |
| --- | --- | --- | --- |
| Claude CLI reference `/docs/en/cli-usage` | 2026-07-15 rolling docs + installed 2.1.210 help | print/stream, UUID, resume/fork/name, settings, permissions, structured output | reconciled |
| Claude sessions | 2026-07-15 rolling docs | scoping, resume/fork/name, concurrency, retention | reconciled with probes |
| Claude Agent View | 2026-07-15 research preview + installed 2.1.210 | supervisor, IDs/states, attach/stop/respawn/rm, worktrees | reconciled with probes |
| Claude hooks | 2026-07-15 rolling docs + installed events | receipt/attention schemas and failure semantics | reconciled with probes |
| Claude settings | 2026-07-15 rolling docs | precedence/merge and no-mutation strategy | verified by composition/hash guard |
| Claude Remote Control | 2026-07-15 research preview | explicit external product boundary | documented only; no live mutation |
| zmx docs and tagged v0.6.0 source | 2026-07-15 / 0.6.0 | PTY/send/security semantics | reconciled with isolated fake target |
| Dispatch source/ADRs | commit 06ae574 baseline | provider/storage/handler/selector seams | audited |

## Cleanup Audit

```text
2026-07-15 final experiment cleanup
- Agent View: both created short IDs stopped and removed; --all/cwd-filtered matching count = 0.
- zmx: isolated namespace matching count = 0; pre-existing default namespace count remained 1 and was untouched.
- Processes: matching disposable process count = 0.
- Temp repository/settings/logs: owned /tmp/dispatch-claude-control-9ae658fe-... root removed.
- Settings: user hash remained 52a8b8fe...; repository-local hash remained 378cd942...; sizes/mtimes and absent paths unchanged.
- Existing Agent View entries and default zmx session were never opened, read, messaged, attached, interrupted, renamed, stopped, or removed.
- Claude's documented local transcript retention remains provider-owned. The research did not locate, read, or manually delete transcript files; Agent View rm is not transcript delete.
```

## Capability Matrix

No row remains pending or unknown. Full semantics and citations are in
`docs/research/claude-control-plane-verification.md`.

| Operation/capability | Status | Primitive/composition | Preconditions | Acceptance/completion receipts | Failure/recovery and consequence | Evidence/version/confidence |
| --- | --- | --- | --- | --- | --- | --- |
| durable identity | verified | caller-chosen full UUID | correct project/worktree; provider-qualified route | same UUID in stream/hooks | typed stale/not-found; names/short IDs never authority | observed 2.1.210, high |
| new | verified | `--session-id UUID --print` | persist lane/message; hook preflight; no frame written | aggregate prompt settlement + activity; final settled Stop cycle + result/exit | retry only when no frame write began | observed 2.1.210, high |
| owned resume-for-send | verified | fresh Dispatch-owned `--resume UUID` | owned identity; correct cwd; no active attempt | `SessionStart(resume)` then aggregate receipts | typed stale/not-found; never `--continue` | observed/docs 2.1.210, high |
| human Agent View attach | verified | human `attach SHORT_ID` | known Agent View entry and operator TTY | UI plus ordinary hooks; not Dispatch transport | no shell reply RPC | observed 2.1.210, high |
| Dispatch attach unmanaged ordinary UUID | unsupported | none proven | n/a | none | do not grant writable authority from UUID alone | surface audit, high |
| send | verified | one serialized fresh resume process | owned identity; preflight; one-writer lease | processing after terminal non-blocking hook settlement/activity; completion after final Stop settlement/result/exit | possible-write loss is indeterminate; never auto-retry | observed 2.1.210, high |
| steer during active turn | unsupported | no documented print-process RPC | n/a | none | typed unsupported; do not relabel queue | official/local audit, high |
| durable queue/readiness | product-decision | Dispatch queue | explicit root allowlist; terminal receipt; healthy hooks; no attention/background work | next aggregate receipt sequence | uncertainty blocks drain until operator resolution | evidence high, policy DIS-52 |
| interject | product-decision | interrupt, audited abandon, then ordinary send | verified owned group; explicit abandon if processing possible | transport exit plus new message receipts | non-atomic; prior provider completion remains unknown | observed 2.1.210, high |
| context injection | unsupported | no safe Claude equivalent | n/a | none | typed unsupported; never convert silently to user text | surface audit, high |
| stop/interrupt | verified | SIGINT verified owned process group | matching pid/pgid/start identity/generation | process exit proves transport interruption only | after processing, completion stays unknown; explicit abandonment may release lease | observed 2.1.210 exit 130, high |
| tail/history | product-decision | normalized live events only for watch | explicit future transcript-retention policy for history | event cursor only | history default off | docs/audit, high |
| watch/events | verified | advisory hooks + owned stream | active owned generation; bounded schemas | hook/source IDs, session/prompt IDs, occurrence order | generation fence/replay dedupe; same-UID spoofing outside nonce guarantee | observed 2.1.210, high |
| rename | product-decision | startup name; human UI later | UUID remains authority | metadata only | no documented scriptable later rename | docs/help, medium |
| archive/restore | unsupported | Agent View rm is removal, not archive | n/a | none | transcript remains resumable; no parity claim | docs + observed cleanup, high |
| goal loop | product-decision | Dispatch goals may send ordinary turns | ordinary send capability | normal aggregate receipts | no forced provider goal-mutation parity | docs/audit, medium |
| permissions/approval | verified | permission mode + request hooks | owned generation; managed policy remains higher | request/decision plus later activity/cycles | failure stays attention/unknown; never implicit allow | observed AskUserQuestion, high |
| user input/elicitation | product-decision | attention hooks + human Agent View response | known Agent View entry and human | shared prompt ID plus later aggregate completion | first slice observes only | observed 2.1.210, high |
| structured output | verified | print `--json-schema` | opt-in request/schema | validated result plus aggregate completion | typed schema/provider failure | official/help 2.1.210, medium-high |
| rich input/files/images | product-decision | provider resources/human paste | future local input contract | ordinary aggregate receipts | first slice text-only | official/help, medium |
| process restart/recovery | verified | fresh resume; Agent View respawn | persisted UUID/generation; no blind replay | resume lifecycle and later receipts | provider-partitioned recovery only | observed 2.1.210, high |
| duplicate/concurrent send | verified | provider creates independent turns | absent Dispatch one-writer/dedupe controls | distinct prompt IDs and receipt cycles | ambiguous retry duplicates; serialize before spawn | observed 2.1.210, high |
| remote/mesh compatibility | product-decision | owning daemon executes; Remote Control separate | explicit external policy/config | owner receipts if enabled | relay auth/outage semantics require decision | official docs, high |

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | transport | `tmp/review-history/transport/round-1.json` | 2/5 | changes requested, fixes applied pending rerun | 7 at review time | aggregate receipts, retry, interrupt state, event identity, pgid, preflight, reproducibility |
| 1 | security/product | `tmp/review-history/security-product/round-1.json` | 2/5 | changes requested, fixes applied pending rerun | 6 at review time | same-UID trust, attach split, prompt digest removal, cleanup contract |
| 2 | transport | `tmp/review-history/transport/round-2.json` | 3/5 | changes requested, fixes applied pending rerun | 4 at review time | ADR-0023, interject, replay identity, mechanical probes |
| 2 | security/product | `tmp/review-history/security-product/round-2.json` | 3/5 | changes requested, fixes applied pending rerun | 4 at review time | cleanup bound, fail-open settlement, queue trust input, README command |
| 1 | full stack | `tmp/review-history/full-stack/round-1.json` | 2/5 | changes requested, fixes applied pending rerun | 6 at review time | ADR, migration, reactor ingress, issues, settings generator, RETRO |
| 3 | security/product | `tmp/reviews/security-product/round-3.json` | 5/5 | clean | 0 | all prior security/product findings fixed |
| 3 | transport | `tmp/review-history/transport/round-3.json` | 4/5 | changes requested, fixed in later probes | 1 | mechanical end-to-end assertions |
| 2 | full stack | `tmp/review-history/full-stack/round-2.json` | 3/5 | changes requested, fixed | 4 | accepted ADR, concrete persistence, selector ordering, preflight variable |
| 4 | transport | `tmp/review-history/transport/round-4.json` | 4/5 | changes requested, fixed | 1 | aggregate settlement and process-exit assertions |
| 3 | full stack | `tmp/reviews/full-stack/round-3.json` | 5/5 | clean | 0 | all prior full-stack findings fixed; live Linear reconciled |
| 5 | transport | `tmp/review-history/transport/round-5.json` | 4/5 | changes requested, fixed | 1 | structured block parsing and Stop exit-2 rejection |
| 6 | transport | `tmp/reviews/transport/round-6.json` | 5/5 | clean | 0 | all TR-001 through TR-010 fixed |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |
| packet prompt | `check-goal-prompt --no-placeholders` | passed | 3,994/4,000 characters |
| packet doctor | goal packet | expected review blockers before final reruns | packet structure otherwise valid |
| shell fixtures | all `spikes/claude/*.sh` | passed | `sh -n` |
| JSON fixtures/settings | tracked JSONL plus all generated settings modes | passed | jq parse; settings mode 0600 |
| process group | `uv run python spikes/claude/process_group_probe.py` | passed | parent/child pgid and cleanup |
| spike lint | `uv run ruff check spikes/claude/process_group_probe.py` | passed | import/noqa fixes applied |
| probe assertions | aggregate replay + three negative completion fixtures | passed | hook pairing, continuation, and nonzero-exit failures enforced |
| repository gate attempt 1 | `just check` | 691 passed, 1 timing failure | focused supervisor test immediately passed; unrelated tracked code unchanged |
| repository gate attempt 2 | `just check` | 692 tests passed; package-content step raced | concurrent review gate rebuilt `dist`; standalone package check passed |
| repository gate | `just check` | full-stack reviewer passed | 692 tests plus build/package; final local rerun pending after transport review |
| final packet doctor | goal packet plus three current clean reports | passed | prompt 3,994/4,000; review reports all 5/5 clean |
| final repository gate | `just check` | passed | Ruff, format, strict mypy, 692 passed/17 deselected, wheel/sdist and contents |

## Prompt / Goal Alignment

- Preparation review passed on 2026-07-15.
- `PROMPT.md` delegates the same authority, boundaries, evidence contract, review gates, stop rules, and ready-PR horizon defined by `GOAL.md`.

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| DIS-9 | In Progress | Description corrected to aggregate receipt findings; implementation order comment added; close only after ready research PR |
| DIS-48 | Todo | Adjacent usage-capture lifecycle; not messaging transport |
| DIS-49 | Todo | Provider selector/CLI shorthand issue retained; blocked by DIS-50 and related to later slices |
| DIS-50 | Backlog | High-priority vertical Claude walking skeleton; blocked by DIS-1/2/3 |
| DIS-51 | Backlog | High-priority receipt reduction and generation recovery hardening; blocked by DIS-50 |
| DIS-52 | Backlog | High-priority queue, attention, and destructive indeterminate-attempt resolution; blocked by DIS-50/51 |
| DIS-53 | Backlog | Metadata-only Agent View evaluation; blocked by DIS-50/52; never grants authority |

## Final State

- Completion proof: durable UUID, cross-process send, aggregate prompt receipt, repeated Stop cycles, interrupt/resume, attention, second message, and preflight verified in disposable sessions.
- Walking-skeleton proof: control primitive verified; production implementation intentionally not present.
- Capability verdict: every row resolved as verified, unsupported, or product-decision; no naked unknowns.
- Implementation plan: provider boundary, additive identity migration, event ingress, receipts, supervision, settings, recovery, security, surfaces, tests, rollout, and issues settled.
- Review summary: transport round 6, security/product round 3, and full-stack round 3 are each 5/5 clean with zero P0-P2.
- Verification summary: packet, fixture, lint/type, sanitizer privacy, process-group, negative receipt checks, and final local `just check` pass; PR CI pending.
- Cleanup audit: research-created Agent View/zmx/process/temp resources removed; settings hashes unchanged; provider-retained transcripts not read or manually deleted.
- Remaining product decisions/blockers: queue/attention (DIS-52), Agent View metadata (DIS-53), selector surface (DIS-49), and optional capabilities; none block an honest DIS-50 plan.
- Recommended first implementation slice: DIS-50 vertical walking skeleton behind disabled config; no abstraction-only precursor.
