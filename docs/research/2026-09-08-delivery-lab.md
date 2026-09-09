# PAT-176 — Dispatch delivery lab, September 8, 2026

**Result:** real Dispatch-owned threads support idle delivery, non-canceling busy
queue delivery, context injection and clean idle daemon restart in this sample.
Live steering fails on the tested protocol shape. Duplicate events produce
duplicate turns. Desktop-to-Dispatch delivery and ambiguous-outcome recovery
remain unproven; this is not an unattended-delivery readiness result.

Tracked in [PAT-176](https://linear.app/outfitter/issue/PAT-176/prove-outpost-delivery-into-codex-threads-using-dispatch).
Predecessor: Grid `research/notes/2026-09-08-outpost-dispatch-codex-delivery.md` and
[Outpost MCP companion](https://app.notion.com/p/3d51b36f46cb8105b9f4edcf12862dd0).

The lab ran source commit `630051a`, package version `0.11.0`, on Studio using
Codex CLI `0.153.4`, model `gpt-5.3-codex-spark`, effort `low`. Model selection came
from the isolated live catalog. Dispatch owned a stdio App Server subprocess.
The repo's ScenarioRunner supplied temporary `DISPATCH_HOME`, `CODEX_HOME` and
work directories plus its documented auth-file copy. No production daemon was
repaired or restarted, no shared socket was used, and no global configuration or
Buzz identity was changed. Envelopes named `mock-buzz` and `synthetic-lab`; no
real Buzz message, reaction or reply was sent.

Expected outcomes were written before execution in the local
`.agents/notes/pat-176/plan.md`. Success required observed completed turns and
assistant text, not just an accepted ACK. The following counts are **total turns
within that run**, with the expected count before the slash and observed after.
Timings measure CLI ACK to the recorded observation, so they include polling
delay and are not provider latency benchmarks.

| Case / source event | Thread | Expected / observed | Evidence and outcome |
| --- | --- | --- | --- |
| Idle `evt-idle-001` | A | 1 / 1 | **Pass.** `IDLE_001`, observed 5.18 s after ACK; sent receipt. |
| Busy + queued `evt-busy-001`, `evt-queue-001` | A | 3 / 3 | **Pass.** Prior idle turn plus busy turn plus one queued turn. Queue 1 was pending and receipt created while busy. `BUSY_DONE` preceded `QUEUE_001`; queue and `queue:1` receipt became sent. Queue delay to sent receipt 15.07 s; final observation 18.22 s after queue ACK. |
| Steer `evt-steer-base`, `evt-steer-001` | B | 1 / 1 | **Fail for requested behavior.** Busy turn had no Dispatch active ID; steer exited 2, “no active turn to steer.” Original turn completed `ORIGINAL_FINAL`, not `STEER_001`. No extra turn. Reproduced after a 2 s delay; raw event evidence below. |
| Idle context `evt-context-001` | B | 1 / 1 | **Pass.** ACK op `brief`; no new turn after 1.55 s. Injection text was absent from `tail`, but an explicit `evt-recall-001` turn returned `CONTEXT_001` and raised total to 2. |
| Busy context `evt-context-busy-base`, `evt-context-busy-001` | B | 3 / 3 | **Pass at this scheduling point.** Active bounded task changed its final reply to `CONTEXT_BUSY_001`; no extra turn. Observed 14.31 s after injection ACK. This does not promise identical behavior at every injection timing. |
| Duplicate `evt-duplicate-001` twice | B | 5 / 5 | **Limitation reproduced.** Starting from 3 turns, identical envelopes caused two additional completed turns and two `DUPLICATE_001` replies. No exactly-once behavior. |
| Idle daemon down/up, `evt-restart-001` | B | 6 / 6 | **Pass for clean restart.** Same five history turn IDs after restart; one new `RESTART_001` reply observed 3.74 s after ACK. While down, status exited 8 with a missing-socket error. No automatic ambiguous send retry was attempted live. |
| App-created task, `evt-desktop-001` | C | 2 / 2 app turns | **App delivery pass; Dispatch delivery inconclusive.** Supported app creation returned `DESKTOP_READY_176`; after completion, app follow-up returned `DESKTOP_REPLY_176` in 2.15 s. Isolated Dispatch `get` found no managed thread; a separate isolated `attach` failed with App Server -32600 “thread not loaded.” No Dispatch turn was started there. |

Thread identities:

- A: `01a08340-5251-7d23-87b0-50f6e1911115`, local ref `0DzNr1`.
- B: `01a08341-cd1f-7e73-8721-3fc0d0c1b4a8`, local ref `0ACpq1`.
- C: `01a08340-6601-71f1-9ebe-2d9c6a1426c4`, app-created disposable task, now archived.

**The steering failure has a concrete protocol reproduction.** The captured
`turn/started` params contain `threadId` and `turn.id`; there is no top-level
`turnId`. `client/events.py:198` reads only the top-level field and produces
`TurnStarted(..., turn_id=None)`. The reactor records that missing value and
`core/handlers.py:534` requires an active ID for steer. Replaying the captured
event through the actual projector returned `None`. Raw completion events also
contain nested `turn.id`. The current event tests pass despite this sample;
passing canned tests is not evidence of live compatibility.

**Receipts remain acceptance bookkeeping.** In the live runs, direct receipts
had `dispatch_message_id=null`, `turn_id=null`, `status=sent`, with no accepted or
completed timestamp. The queued receipt had internal ID `queue:1` but also no
turn ID or completion timestamp. The public ACK exposed no structured delivery
or turn ID. Correlation in this report comes from synthetic tokens, ordered
submissions, raw events and persisted history; the adapter cannot obtain this
mapping from the ACK alone. Local `mock-replies.json` illustrates outbound reply
mapping by that harness evidence and was never sent to Buzz.

Six additional **mock handler/Registry cases** passed using the existing
FakeLaneClient, real handlers and temporary Registry stores:

| Mock case | Expected / observed provider starts | Meaning |
| --- | --- | --- |
| Duplicate external event | 2 / 2 | Event ID in text does not deduplicate direct sends. |
| Steer with seeded active ID | 0 / 0 new turns | Routes one `turn_steer`; says nothing about live event projection or model behavior. |
| Context | 0 / 0 new turns | Routes one `inject_items`; no model invoked. |
| Busy queue, then idle drain | 1 / 1 | Pending/created while busy; internal queue receipt becomes sent. |
| Accepted, ACK lost, reopen Registry, retry | 2 / 2 | Artificial provider acceptance followed by TransportError leaves failed receipt. Retry after reopen creates a second acceptance and a sent receipt. |
| Queue accepted, crash before bookkeeping, reopen/reset/drain | 2 / 2 | Artificial crash leaves row sending and receipt created. Assuming recovery establishes idle, startup reset/drain resends it. One final `queue:1` sent receipt hides the first acceptance. |

The last two tests inject faults at explicit seams. They are not live provider
crash/reconnect proof. The queue case assumes the first provider accepted, the
process died before local completion, the same Registry survives, and recovery
establishes idle. Its source path is `core/queue.py:128`, which resets sending
rows before draining idle queues.

**Desktop boundary:** the supported app tool can create a task but cannot choose
the lab's isolated Codex store. Reading a foreign ID from a separate temporary
store is not a writer-handoff test. This lab deliberately did not read/copy live
Desktop history or enable a second writer against it. App-visible reply evidence
comes from `wait_threads`/`read_thread`, not a visual screenshot. The task had
one app writer, sequential turns and no tools; it was archived after evidence
capture. Attached-write policy and Desktop/Dispatch coexistence are unproven.

Smallest follow-up: first repair the nested turn-ID projection with the captured
wire fixture, then rerun one busy steer case. Before unattended retry, add a
caller event key bound to destination/payload, typed delivery/state/turn
correlation and public receipt lookup; ambiguous provider outcomes need an
explicit reconciliation policy, including the queue crash window. Use
Dispatch-owned threads for an initial connector proof. Desktop delivery requires
a separately authorized supported shared-store/connection and single-writer
handoff experiment. Do not substitute context injection for reliable steering
merely because this one busy-context sample changed the reply.

Artifacts are local to this worktree:

- `spikes/delivery_lab/`: reusable opt-in harnesses and operating README.
- `.agents/notes/pat-176/scenario-summary.json`: per-case IDs, turn counts,
  responses, timings and receipt snapshots.
- `live-attempt-2.json`: live idle/queue and first steer failure; `live-results.json`:
  narrowed remaining live cases and raw event watch.
- `event-projection-repro.json`, `mock-results.json`, `desktop-results.json`,
  `desktop-dispatch-boundary.json`, `mock-replies.json`, `cleanup-verification.json`.

The `.agents/notes` evidence is gitignored, so preserve it with this worktree.
One initial harness attempt mistakenly expected a `turns` array from `tail`; it
was stopped and corrected to count item turn IDs. Its evidence is retained as
`live-attempt-1.json`. Three Dispatch-owned disposable threads were created across
that setup correction, the first run and the narrowed run. Fatal-run cleanup
can end a disposable outstanding turn; ordinary messages did not request
interruption. All temporary homes/copies were removed and isolated daemons
stopped; production services and work threads were untouched.

Validation: six mock scenarios passed; Ruff check/format and Python compilation
passed for lab scripts. The targeted event/handler/reactor suite passed **22
tests**. No full `just check`, hosted CI, live provider fault injection, deployment
or production fix was performed. Final reusable-runner argument/output-path
cleanup was linted/compiled without another full live replay; the observed
results come from the recorded runs above.

## Follow-up: isolated compatibility package

The historical findings above describe the unpatched baseline. DIS-71 is now
implemented in local commit `895167778d33f4f89d1c7f60402453d3a9ab687f` on
`dis-71-preserve-nested-codex-lifecycle-turn-ids-for-active-turn`. The client
projection accepts nested `params.turn.id` when legacy `params.turnId` is absent.
A captured-shape synthetic fixture drives the reactor and real send handler;
the test failed before the fix and passes for both nested and legacy events.
Completion clears active state while preserving the final turn ID.

`just check` passed: 960 tests, 17 deselected, Ruff, formatting, strict mypy,
package build and package-content validation. Independent local review found no
actionable findings. The wheel was installed into an isolated virtual environment
with dependencies constrained by the repository lock export. Installed event
projection source matches the wheel and local source. The normal installed
Dispatch executable was not replaced.

- Executable: `.agents/notes/pat-176/custom-compat/venv/bin/dispatch`.
- Wheel: `.agents/notes/pat-176/custom-compat/dist/outfitter_dispatch-0.11.0-py3-none-any.whl`.
- Wheel SHA-256: `f1d190a4cf23b6d22fc893c91c3e6100733a269d3a1add33cd1741f5cdf76654`.
- Manifest: `.agents/notes/pat-176/custom-compat/manifest.json`.
- Full live evidence: `.agents/notes/pat-176/live-1788909774196084000.json`.

All 11 assertions passed across nine completed turns: idle, busy queue, busy
steer, idle context recall, busy context, duplicate baseline, and clean daemon
restart. Steering changed the existing turn reply to `STEER_001` without
creating another turn. Queue ordering, context producing no separate turn, and
history preservation across restart passed. Duplicate envelopes still produced
two replies, as expected for the unchanged reliability behavior. Temporary
Dispatch/Codex/work directories and copied credentials were removed and the
isolated daemon stopped; the custom package is retained for inspection.

No hosted CI, push, production installation, deployment or live crash injection
was performed. Desktop attachment and reliable unattended retry remain unproven.

## Proposed separate reliability slice

The ACK-loss and queue-crash reproductions require a durable delivery ledger,
not another lifecycle compatibility patch. The smallest useful first scope is
text send and busy queue delivery on Dispatch-owned Codex threads. Leave steer,
context and Desktop writer coordination out of its initial claim.

1. Accept an optional caller idempotency key, unique within a Registry, bound to
   the resolved immutable destination and normalized delivery payload/settings.
   A matching retry returns the same receipt; different bound input raises a
   typed conflict. Resolve mutable selectors before binding. Retain generated
   delivery IDs for callers that omit a key, with no deduplication promise.
2. Expose typed receipt lookup through one authored op and its derived surfaces.
   Record delivery ID, key, destination, submission state, timestamps, known
   provider turn ID and error evidence. Distinguish queued, submitting, accepted,
   completed, definitely failed and ambiguous. Accepted does not mean completed.
3. Reserve the key and queue entry in one local transaction. Claim a queued
   entry durably before the provider call, without holding a database transaction
   across network I/O. Replace unconditional startup replay of sending rows with
   ambiguous recovery when submission may have reached the provider.
4. Never automatically resubmit an ambiguous receipt. A missing acknowledgment
   or daemon crash is not proof of rejection. Reconcile only with sufficient
   provider evidence, preserving the receipt identity and known turn association;
   account for lifecycle events arriving before the request acknowledgment.
   No transaction can make local SQLite and the provider acceptance atomic.
5. Recommended unresolved policy: hold later queued delivery for that thread
   while an earlier submission is ambiguous, and require an explicit resolution
   that acknowledges the uncertainty. Merely observing idle does not establish
   whether the earlier delivery happened. A new key is not a safe retry recipe.

This needs a schema migration, registry methods, handler/queue changes and
contract tests, so it should be a separate reviewed change. Acceptance should
cover same-key concurrency, payload/destination conflicts, durable lookup after
restart, accepted-but-ACK-lost, crash after queue claim/acceptance, event-before-ACK
ordering, and proof that recovery performs no second provider submission.
The policy for releasing a queue held by ambiguity needs agreement before that
behavior is implemented. The DIS-71 fix and package are complete independently.

## Approved DIS-72 implementation and package proof

The proposal above was approved with the per-thread hold policy. DIS-72 is now
implemented in local commit `169b0fc1d308fd92d05384d197e1ccaab23cf387`, stacked on
DIS-71. The initial scope is opt-in keyed plain-text send/queue; unkeyed sends do
not gain deduplication. The generated receipt ID is passed through Codex's
`clientUserMessageId` and persisted as `userMessage.clientId`, verified live on
0.153.4. No marker is added to the visible prompt.

Schema 22 atomically reserves the key/queue entry and claims it before submission.
Lost ACKs and interrupted claims become ambiguous, hold subsequent queue entries
on that thread, and never automatically resend. Three bounded history checks
require unique native ID plus exact input. Explicit `delivery reconcile` adds one
bounded read without resending. Positive acceptance plus a separate current idle
observation can release the queue, guarded against overwriting newer local state.
`delivery get` exposes receipt state and separate execution status. See
[operator contract](../usage/deliveries.md) for bounds and failure semantics.

The first installed live send exposed public datetime receipt serialization at
the control socket. A real socket regression reproduced the failure before the
fix. Public timestamps now follow the existing string convention; internal
receipts retain typed datetimes. The rebuilt executable passed all seven live
cases over three turns: same-key replay concurrency, conflicting reuse, lookup,
clean restart replay, busy queue, completion and persisted native receipt IDs.
The four replay callers ran concurrently after the first reservation; competing
initial reservation and injected crash/ACK-loss behavior are covered by CI-safe
tests, not claimed as live crash evidence.

- Full gate: `just check`, 1,012 passed / 17 opt-in deselected, Ruff, formatting,
  strict mypy, build and package-content validation.
- Installed-byte regression: 52 passed; all 60 imported Dispatch modules came
  from the private environment, and every source Python file matched the wheel
  and installed package byte for byte.
- Independent local review: clean, no open P0/P1/P2 findings.
- Private executable: `.agents/notes/pat-176/custom-reliability/venv/bin/dispatch`.
- Wheel SHA-256: `7641bcc3654b1fdf89b3eb7f6c3829dacf1b3163f0ab11a3288e78e7d36ada9e`.
- Manifest: `.agents/notes/pat-176/custom-reliability/manifest.json`.
- Passing live evidence: `.agents/notes/pat-176/reliability-1788912835255726000.json`.
- Earlier failed run: `.agents/notes/pat-176/reliability-1788912582008638000.json`.

Both runs stopped their isolated daemons and removed temporary homes, copied auth
and work directories. Package and synthetic evidence remain local. No installed
PATH executable, production registry or existing Desktop task was modified.

## Invoked-session host capability proof

An auth-only isolated Dispatch session exposed hosted connector apps but not
Desktop app tools or CUA. A separate temporary package overlay registered exact
lab-only dynamic tools on `thread/start` and routed `item/tool/call` through the
existing request manager to supported host handlers via an external lab bridge.
A real `dispatch new` source turn called app task read, send, rename and post-read;
the disposable target completed the delivered turn and its exact new title was
verified. The source persisted five dynamic tool calls and completed. All
external mutations targeted that disposable task, which was then archived.

This proves the callback architecture with an externally mediated prototype,
not native standalone Dispatch access to app-only tools. DIS-73 records the
focused production registration/handler seam and its authority boundaries. The
prototype is `spikes/delivery_lab/dynamic_bridge.py`; exact topology and evidence
are in `.agents/notes/pat-176/dynamic-bridge.md` and its JSON artifacts.

The fifth callback reached the real CUA handler, but the synthetic data URL was
rejected by browser policy and the source received `success:false`. A later
allowed ordinary HTTPS marker observation happened outside that callback. It
does not prove invoked-session CUA success; reversible interaction remains
unproven. No further workaround was attempted after stopping the probe.

These probes provide timestamped observations of adapter reachability, provider
execution, receipt acceptance/completion, queue state and callback success/failure.
They do not establish continuous presence or an idle inbound wake subscription.
A completed turn is execution evidence; a tools inventory or daemon response is
only capability/reachability evidence. No new monitor or poller was installed.

## Exact Desktop writer boundary: supplied browser task

A subsequent parent-coordinated live test used Matt's explicitly supplied idle
Desktop-created task `01a083d0-ff23-74d0-b49a-7380fb4f14ba` (`#Dispatch browser
test`). Matt authorized the custom Dispatch package to attach and send a request
to navigate to `https://github.com/outfitter-dev/trails/pulls`. This observation
was reported by the coordinating task; this lab did not repeat the operation.

The parent used the private `custom-reliability` executable, a new
`DISPATCH_HOME=/tmp/outpost-dispatch-browser-01a083d0`, and the normal Codex home,
explicitly authorized for this exact target. Only the separate test config set
`allow_attached_writes=true`. App Server initialization through `doctor` passed.
Attach succeeded with ref `043fP1`, `writable=true`, and status `idle`.

Exactly one send returned exit 8 with App Server error `-32600`:

```text
thread 01a083d0-ff23-74d0-b49a-7380fb4f14ba already has an active writer
```

A subsequent app task read still showed only the original completed browser turn.
The parent reported no retries, alternate socket attempts or lock bypass, and
was stopping only its separate test daemon. Shutdown completion was not part of
the supplied observation.

This supersedes any assumption that writer interlocks are absent in this tested
Codex 0.153.4 Desktop topology: an idle task can retain an active writer owner.
Successful attach, Dispatch's writable flag and idle execution state do not
establish provider write authority. This result is distinct from the earlier
isolated-home inability to load a Desktop task. It does not establish behavior
for every topology or justify a global flag change or forced handoff. Continuing
through app-owned tools would be a different path, not proof of Dispatch send.
