---
id: 0021
slug: lane-inbox-and-delivery
title: Lane Inbox and Delivery
status: proposed
created: 2026-06-16
updated: 2026-06-16
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0021: Lane Inbox and Delivery

## Context

dispatch already has the bones of a local message bus:

- `queued_messages` stores durable per-lane text and rich-input references that start a turn when the lane becomes idle. Local image bytes are never copied into the registry; local files are revalidated at delivery.
- The reactor drains one queued message on `TurnCompleted` and `LaneIdle`.
- Triggers bind `when -> action -> lane` and audit each firing.
- `actions_log` records command and delivery activity.

That queue is intentionally a **turn delivery queue**. It is not an inbox: a queued
message is meant to become `turn/start`, has no independent acknowledgement state,
and cannot represent "record this for later without interrupting the agent."

Subscriptions, delayed sends, needs-attention routing, mesh messages, approval
handoffs, reminders, and future operator notices all need a durable recipient-facing
record that can be inspected, delivered, retried, or acknowledged independently of
whether it starts a turn.

## Decision

Introduce a lane inbox as the durable coordination layer above turn delivery.

An **inbox message** is a recipient-addressed record. It can be created by
subscriptions, direct messages, triggers, reminders, mesh peers, or system notices.
It is durable, queryable, and acknowledgeable even when no agent turn is started.

Keep `queued_messages` as the low-level turn-start delivery queue. Do not overload it
to represent all pending coordination messages.

Separate the lifecycle into four concepts:

- **Inbox message** — durable record addressed to a recipient lane, with source,
  kind, subject/body, structured payload, created time, and acknowledgement state.
- **Delivery** — optional attempt to surface an inbox message, initially by queuing a
  turn to the recipient lane.
- **Acknowledgement** — recipient/operator clears the inbox message. Delivery and
  acknowledgement are distinct.
- **Turn queue** — existing `queued_messages`; used only when a delivery adapter
  needs to start a Codex turn.

Initial inbox states:

- `pending` — visible and not acknowledged.
- `acked` — acknowledged by recipient/operator or by auto-ack policy.
- `archived` — hidden from default views without implying the recipient handled it.

Initial delivery modes:

- `inbox` — create the inbox message only; do not start a turn.
- `turn` — create the inbox message and enqueue a turn-delivery message.

Initial acknowledgement policies:

- `manual` — message remains pending until `dispatch inbox ack`.
- `auto_on_turn_start` — mark acknowledged once the turn-delivery message is
  successfully started.

The default for inbox-only messages is `manual`. The default for turn-delivered
subscription updates may be `auto_on_turn_start`, because the recipient lane is
woken with the message content.

Expose a small inbox surface:

```bash
dispatch inbox
dispatch inbox --lane <ref>
dispatch inbox --kind subscription_update
dispatch inbox read <message-id>
dispatch inbox ack <message-id>
dispatch inbox ack --all --lane <ref>
```

`self` may resolve from the current Codex/Dispatch context where available. If
Dispatch cannot infer the current managed lane, commands requiring `self` fail with a
clear diagnostic and ask for `--lane <ref>` or `to:<ref>`.

## Consequences

### Positive

- Subscriptions can record useful updates without forcing an agent turn.
- Future features reuse one durable inbox instead of inventing bespoke queues.
- Delivery retries and acknowledgement can evolve without losing the underlying
  coordination event.
- `queued_messages` remains simple: it starts turns when lanes are idle.
- Operators get a first-class pending-work view for agents.

### Tradeoffs

- Adds another persistent table and lifecycle to the registry.
- Requires clear docs so users understand the difference between inbox messages,
  deliveries, acknowledgements, and queued turns.
- Auto-ack policy can hide important updates if chosen too broadly. Defaults should
  favor manual ack for inbox-only messages.

## Alternatives considered

- **Use `queued_messages` as the inbox** — rejected: queued messages are turn
  deliveries. Making them also mean "pending inbox record" would make
  non-interrupting subscriptions awkward and muddy delivery state.
- **Use only `actions_log`** — rejected: the audit log is append-only history, not a
  recipient-facing pending-work surface.
- **Make every subscription start a turn** — rejected: agents may want to accumulate
  updates and acknowledge them later without interruption.
- **Build a general external message bus** — rejected for now: Dispatch needs a local
  lane coordination substrate first. External peers and webhooks can be later delivery
  adapters.

## References

- [ADR-0003: Own Scheduler, Not Codex Automations](0003-own-scheduler-not-codex-automations.md)
- [ADR-0014: Mesh Auth, Discovery, and Durable Queues](0014-mesh-auth-discovery-and-durable-queues.md)
- [ADR-0016: History, Goals, and Bounded Watch](0016-history-goals-and-bounded-watch.md)
