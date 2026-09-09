# Reserved text delivery

Use a stable caller key when a connector may repeat a delivery request:

```sh
dispatch send <thread-ref> "Synthetic work item" --idempotency-key outpost:event-123 --json
dispatch send <thread-ref> "Next work item" --queue --idempotency-key outpost:event-124 --json
dispatch delivery get <receipt-id> --json
dispatch delivery reconcile <receipt-id> --json
```

This contract currently applies to plain-text `send` and `queue` on
Dispatch-owned Codex threads, and plain-text `queue` on attached threads when
`allow_attached_writes = true`. A key with structured content, steer, context or
interject is rejected. Attached queue requests always receive a durable receipt;
omit the caller key only when each invocation should create a separate delivery.
Other unkeyed delivery retains its existing behavior and does not promise deduplication.

Keys are exact, nonblank strings of at most 200 characters, unique within one
Dispatch Registry. Dispatch resolves the thread selector before binding the key
to the destination, mode, text (including any intro), cwd and saved turn settings.
The same key and request return the same receipt, including after restart.
Different bound input raises `delivery_conflict`; changing a title or using a
different selector for the same thread does not change its identity. A changed
saved turn setting is a different request. Omitted settings still delegate their
values to Codex defaults.

Attached queue delivery binds the destination, mode, transport and exact text.
It uses the existing owner's execution settings; Dispatch's saved cwd/model/turn
settings are not applied to the queued input.

## Attached threads and native queue delivery

For a thread already owned by the desktop app, explicitly use `send --queue`.
Dispatch calls Codex's experimental `thread/queue/add` without acquiring writer
ownership or calling `thread/resume`, `turn/start`, or `turn/steer`. The active
owner picks up queued input after its current turn. Cold threads may retain the
input until an owner resumes them. Queue acceptance does not prove execution.
This path was checked against Codex 0.153.4; a provider that rejects the method
returns `capability_unavailable` and a failed receipt, with no writer fallback.

Dispatch submits locally reserved native requests in reservation order per
thread. An ambiguous submission holds later reservations until positive evidence
resolves it. Once the provider accepts a request, its native queue controls
execution order. On an already managed attached thread, an explicit send or steer
encountering another active writer returns a typed capability error; it is never
silently converted into queue input. A first-contact nonqueue request can still
receive the provider's active-writer rejection during history import.

Pre-upgrade local queue rows remain unchanged. Pending or sending legacy work on
an attached thread holds new native submissions, so an upgrade cannot make new
input overtake that work. Inspect and resolve the old queue through its existing
workflow before expecting native reservations to submit; Dispatch does not
migrate or replay those rows into the native queue.

The native acknowledgment supplies a submission ID, not a turn ID. Accepted
receipts can therefore remain execution-unverified until `delivery reconcile`
or reconnect reads the corresponding persisted turn. Dispatch does not receive
another writer's live completion events. Reconciliation checks native queued
input by exact client message ID and text, then persisted full-item turn history.
Disappearance from the native queue alone is inconclusive and never permits resend.

The `send` result includes `delivery`. Its top-level `accepted` field acknowledges
the Dispatch request; consult the delivery status for provider progress:

| Status | Meaning |
| --- | --- |
| `queued` | Reserved locally; not submitted yet. |
| `submitting` | A durable claim exists; provider outcome is not yet recorded. |
| `accepted` | Provider acknowledgment or exact persisted user-input evidence proves arrival. |
| `completed` | The correlated provider turn completed. |
| `failed` | A protocol-level rejection or failure before provider submission proves this attempt did not run. |
| `ambiguous` | The provider may have accepted it; no safe conclusion yet. |

`execution_status` separately records in-progress, completed, failed or
interrupted execution when known. A turn that fails after acceptance remains
accepted with `execution_status: failed`; it is not a rejected submission.
Receipt lookup returns IDs, state, error evidence, timestamps and check count,
without returning the retained request payload.

## Uncertain outcomes and reconciliation

Dispatch commits the reservation before calling Codex. Queue insertion and the
receipt link share one local transaction; claiming a receipt and queue row is
also atomic locally. A database transaction cannot make provider acceptance
atomic with local bookkeeping.

Lost acknowledgments, transport errors and interrupted submissions can leave an
ambiguous receipt. Reopening the daemon changes unfinished `submitting` attempts
to ambiguous; it does not reset them for resend. Such receipts hold later queued
work on that thread, including unkeyed queue entries. Other threads continue.

Codex 0.153.4 was verified to accept `clientUserMessageId` on `turn/start` and
persist it as `userMessage.clientId`. Dispatch uses the generated receipt ID there
without inserting a visible marker into user text. Reconciliation requires one
exact ID and matching user text in one provider turn, with complete full-item
history inside the scan budget. Assistant prose and local attempted-send logs
are never acceptance evidence.

Automatic reconciliation performs at most three checks per ambiguous receipt,
with two seconds between worker cycles. Each history scan has an eight-second
deadline, at most four pages of 50 turns, and a 1 MB serialized-history budget.
Native queue lookup uses at most four seconds within that deadline, leaving time
for persisted history if the queue is unavailable. Conflicting input, duplicate
queue identities, or an incomplete scan after matching input remain unresolved
for inspection rather than falling through.
Missing entries, truncated history, repeated cursors, missing full items,
conflicting content and duplicate IDs remain inconclusive. Exhaustion persists
actionable attention in the receipt and daemon log; it never triggers a resend.

`delivery reconcile` performs one additional bounded check when the operator
requests it. It can resolve late visibility after automatic checks end, and can
refresh execution/readiness for an already accepted receipt. It does not resend
the request or reset the automatic budget. Changing the caller key is not a safe
retry for an ambiguous delivery.

A completed Dispatch-owned receipt can also be reconciled explicitly to retry an unsuccessful
readiness check. This skips history and the original submission, and drains at
most one later local queue entry after confirming idle. Failed or interrupted
execution never authorizes that drain. Native queue receipts leave readiness and
queue draining to the existing app owner, including during manual reconciliation.

After positive acceptance evidence, a separate bounded metadata read may confirm
the destination is currently idle. Dispatch applies that observation only if
the captured local thread state is unchanged and no newer submission is active.
This prevents an old history completion from overwriting a newer busy turn.
Readiness gets at most three metadata attempts within one eight-second deadline;
a current busy response or a newer local event ends the check immediately. If
those attempts fail, queued work stays held until an explicit reconcile or an
authoritative idle event confirms readiness. There is no unlimited background poll.
Reconnect also checks acknowledged work whose completion notification may have
been missed; acceptance survives even when completion history is unavailable.

The Registry retains the normalized request needed for binding and exact input
comparison. This adds schema version 23; an older executable cannot open that
upgraded Registry. The private lab package is tested with isolated homes. New
receipt operations fail closed against pre-handshake daemons that lack them;
unchanged compatible reads remain available.

This is one-attempt reservation and evidence-based recovery, not an exactly-once
provider guarantee. Rich-input deduplication and live provider crash behavior
remain separate validation scopes.
