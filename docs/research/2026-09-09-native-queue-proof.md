# Native attached queue proof

DIS-74 extends the DIS-72 reservation ledger to explicit plain-text queue requests
on attached Codex threads. The native queue owns scheduling; Dispatch reserves
and correlates one submission without acquiring the existing writer connection.

## Protocol and implementation

The protocol was checked against local Codex 0.153.4 and official source commit
[`3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`](https://github.com/openai/codex/commit/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a).
Experimental `thread/queue/add` accepts `threadId`, `input` and
`clientUserMessageId`, returning a queued submission ID without a turn ID.
The client message ID is correlation, not provider deduplication. A lost response
must never cause another add call without positive rejection evidence.

Dispatch commits a native receipt before submission, returns it for exact-key
replays, and submits local native reservations in per-thread reservation order.
Ambiguous receipts hold later reservations. Bounded reconciliation checks exact
native queue entries, then full-item persisted turn history. Missing entries
remain inconclusive. Accepted receipts retain unverified execution until explicit
reconciliation or reconnect establishes a correlated turn.

First-contact registration uses metadata only. The pre-release installed-wheel
run 007 caught an initial history import calling resume and receiving an
active-writer rejection before reservation. A regression reproduced that call;
the corrected path performs only metadata read followed by native queue add.
The two subsequent accepted 007 requests completed and were reconciled; their
order is not used as evidence for the fresh first-contact run below.

## Installed-wheel run 008

On September 9, the private wheel was installed into a dedicated virtualenv with
the repository's locked production dependencies. Its SHA-256 was
`f00aa297dd4895c6c184b8c7587506fa1b5540cd7776ab3a447238671c013fe1`.
The executable was `.agents/notes/pat-176/custom-native/venv/bin/dispatch`.
An empty isolated Dispatch home used the normal Codex home only for the explicitly
authorized test thread `01a083d0-ff23-74d0-b49a-7380fb4f14ba`.

The desktop owner ran a bounded sleep turn. During that turn, the installed CLI
queued synthetic A and B in order, then replayed A's exact key and payload.

| Request | Receipt | Native submission | Correlated turn |
| --- | --- | --- | --- |
| A | `1a077dcf-0e9f-4cad-a5af-d85633da08df` | `01a08747-5c92-7aa1-9b9e-770e3a4bc8c4` | `01a08747-c0bc-76c1-91b1-a68464631e80` |
| B | `e167692b-2f55-45b0-85d5-2652fa7eadc4` | `01a08747-5ea2-73a2-a077-76ade5edba4e` | `01a08747-ca4a-72a3-8ea9-bd9eb459bfd5` |

A and B were accepted at 17:46:36.048Z and 17:46:36.577Z, respectively, with null
turn and execution fields. A's replay returned an identical receipt, including
submission ID and timestamps. Explicit reconciliation later returned completed
status and completed execution for both, with no error.

Persisted rollout records establish this order:

1. Owner final `DISPATCH_NATIVE_OWNER_008_DONE` at 17:47:01.389Z.
2. A user input at 17:47:02.131Z; final `DISPATCH_NATIVE_SOURCE_A_008` at 17:47:03.843Z.
3. B user input at 17:47:04.596Z; final `DISPATCH_NATIVE_SOURCE_B_008` at 17:47:07.499Z.

Assertions over response-message records found exactly one user input and one
assistant final for each marker. Provider user-message events carried the exact
receipt IDs. The app reported the target idle after these two completed turns.

The target writer lock after the run belonged to PID 30209. A fresh before-run
target-lock snapshot was not captured in this run; the coordinating task had
previously identified the same PID. An initial snapshot of a different App Server
PID, 32220, is explicitly excluded from ownership evidence. Behavioral proof is
the continuing owner turn and its subsequent consumption of both native requests.

Both isolated Dispatch daemons were stopped. The target remained unarchived and
idle. No global executable installation, release, or production configuration
change was part of this proof.

## Checks and evidence boundaries

`just check` passed 1044 tests, with 17 opt-in tests deselected, plus Ruff, strict
mypy, build and package-content checks. Regressions cover native key replay and
concurrency, uncertain response recovery without resend, first-contact writer
avoidance, queue evidence bounds, schema migration, control-socket projection,
and the FIFO edge where a new request arrives before an older waiting reservation
drains after ambiguity clears.

Independent review subsequently caught a mixed-transport upgrade case: a new
native reservation could overtake pre-upgrade local queue work. The final claim
guard also waits behind same-thread pending or sending legacy rows. Two
regressions prove the hold and release after that work completes. This guard was
added after run 008 and verified in the final 1044-test gate; run 008's wheel
digest above describes the exact live-tested package before this additional fix.

Raw protocol provenance, receipts, filtered rollout records, lock evidence and
the private package manifest are retained locally under
`.agents/notes/pat-176/`. This live run proves normal acceptance, ordering and
reconciliation on the designated loaded desktop thread. Cold-owner pickup and
live provider crash recovery were not exercised by run 008; cold behavior is
supported by upstream source, while injected tests establish local recovery rules.
