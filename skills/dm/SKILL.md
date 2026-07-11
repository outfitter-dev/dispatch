---
name: dm
description: Use when sending a short, synchronous dispatch-backed direct message to an owned Codex lane, asking a bounded question, identifying inter-lane sender/target handles, or teaching a target lane to reply in a lightweight chat style. Long name: dispatch message. Not for delegation, background work, heartbeats, or autonomous task ownership.
metadata:
  short-description: Dispatch message a Codex lane
---

# DM

Use `$dm` for a short, synchronous conversation with another dispatch-managed
Codex lane. The long form is "dispatch message": the workflow is backed by
`dispatch send`, not by a separate `dispatch message` CLI op in v0.

Use a goal/delegation workflow instead when the target lane should own
background work, source-control follow-through, monitoring, heartbeats, or a
long-running task.

## Preconditions

Use dispatch first:

```bash
uv run dispatch doctor --no-app-server
uv run dispatch list
uv run dispatch daemon status
```

If the environment is new, run `uv run dispatch doctor` once before messaging.
Fix PATH, Codex auth, stale daemon files, registry, or plugin asset warnings
before assuming a DM failure is about the target lane.

The target should be an owned dispatch lane selected by dispatch ref when
possible. Attached lanes are turn-write locked by default. Dispatch permits
DM/send verbs only when local config explicitly enables
`[policy] allow_attached_writes = true`; check the lane's `writable`,
`capabilities`, and `write_locked_reason` fields before sending.

If the user wants to message an existing desktop Codex thread, attach/sync it
only for observation unless they explicitly enable attached writes after
reading ADR-0005 and ADR-0018.

## Handles And URIs

Use bare `@Name` handles for conversational identity. When the thread id is
known, make the first human-facing mention a Markdown Codex URI. Compose a link
whose label is the handle and whose destination is the Codex URI:

```markdown
label: @Target
destination: codex://threads/<target-thread-id>
```

Use the dispatch ref or full thread id for `dispatch send`. Use the URI link in
the message body so the recipient and the human can open the thread directly.

## Message Shape

Send one small request with `dispatch send --intro` so Dispatch appends the
standard visible attribution footer and includes the reply hint:

```text
<freeform message>

Contract: read-only, brief answer, reply in this lane unless asked otherwise.

dispatch (dm): [@Sender](codex://threads/<thread-id>) `<ref>`
↳ reply `dispatch send <ref> "..."`
```

Then deliver it:

```bash
uv run dispatch send <target-ref> '<message>' --intro
```

Keep DMs conversational and bounded. Prefer one ask. Include only the context
needed for the target lane to answer without reading the sender's full
transcript. Add Codex thread links inside the freeform message when the recipient
or human needs a direct desktop link. If the current Codex thread is not managed
by dispatch, omit `--intro` and include the sender link manually. Do not use
`$dm` to smuggle in broad implementation work.

## Harvesting

`dispatch send` starts the target turn. Use `dispatch get` and `dispatch daemon log`
to confirm thread state and accepted actions:

```bash
uv run dispatch get <target-ref>
uv run dispatch daemon log --limit 10
```

Important v0 limitation: `dispatch get` is thread metadata unless the CLI
surface requests transcript output. Use `dispatch tail <target-ref> --limit 50`
for persisted history when the lane is non-ephemeral, or open the Codex thread
link. `tail` and selector-scoped `history` read App Server transcript history
and backfill Dispatch's local history index for that thread; bare `history` is
only an indexed overview. Do not pretend a DM harvested text you did not actually
read.

## Optional Contract Lines

Use one line only when it reduces ambiguity:

```text
Contract: answer only; no repo/tracker/source-control mutation.
Contract: 3-6 lines; include evidence paths if you check live files.
Contract: sanity-check the wording; no implementation.
Contract: reply back to <sender Markdown thread link> when done.
```

## Guardrails

- Do not use `$dm` for background execution or task ownership.
- Do not create new lanes unless the user asks.
- Do not pin, archive, rename, or stop lanes as part of ordinary DM flow.
- Do not send secrets, private data, long logs, or unrelated transcript dumps.
- If the exchange becomes work ownership, stop and switch to a goal/delegation
  workflow.

## Example

```text
From <@Docs Markdown thread link> to <@Dispatch Markdown thread link> via $dm:

Quick terminology check: should the usage docs say "lane" everywhere, or is
"thread" acceptable in the operator guide when referring to raw Codex ids?

Contract: read-only; 3-5 lines; include evidence paths if you check repo docs.
```
