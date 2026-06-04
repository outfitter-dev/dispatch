---
id: 0007
slug: normalized-internal-lane-events
title: Normalized Internal LaneEvent Vocabulary
status: accepted
created: 2026-06-02
updated: 2026-06-02
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0007: Normalized Internal LaneEvent Vocabulary

## Context

The reactor and triggers must respond to lane activity. The raw App Server notifications are protocol-shaped dicts that may drift across binary versions, and they are awkward to express trigger guards against (`idle_for`, `turn_completed`, `waiting_on_approval`). Leaking raw protocol events into the trigger system would couple triggers to the wire format and make the conditional-guard seam hard to define.

## Decision

The **client layer** projects raw App Server notifications into a typed internal `LaneEvent` union — the single translation point. The reactor, triggers, and the conditional-guard seam operate **only** on `LaneEvent`s, never on raw protocol dicts. Initial vocabulary (extend as needed):

- `TurnStarted`, `TurnCompleted`, `TurnFailed`
- `LaneIdle` (derived from status → idle)
- `ApprovalRequested` (command / file-change)
- `ItemCompleted`, `DiffUpdated`
- `StatusChanged`, `TokenUsageUpdated`

Each carries lane id, turn id where applicable, and a typed payload.

## Assumptions

- This vocabulary covers v1 trigger needs (`idle_for`, `turn_completed`, `waiting_on_approval`); new event types are added at the client boundary as needs appear.
- Derived events (e.g. `LaneIdle`) are computed in one place (the client/router), not re-derived per consumer.

## Consequences

- Triggers stay stable across App Server protocol drift; only the client's projection changes.
- Guards and (future) conditional triggers are expressed against a clean, typed model.

## Alternatives considered

- **Operate on raw protocol notifications** — rejected: brittle, leaks the wire format into triggers, hard to guard against.

## References

- ADR-0003 (Own Scheduler); `.claude/rules/client.md`; `docs/research/app-server-verification.md` (notification grammar).
