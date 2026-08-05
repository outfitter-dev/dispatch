---
id: 0007
slug: normalized-internal-lane-events
title: Normalized Internal LaneEvent Vocabulary
status: accepted
created: 2026-06-02
updated: 2026-07-15
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0007: Normalized Internal LaneEvent Vocabulary

## Context

The reactor and triggers must respond to lane activity. The raw App Server notifications are protocol-shaped dicts that may drift across binary versions, and they are awkward to express trigger guards against (`idle_for`, `turn_completed`, `waiting_on_approval`). Leaking raw protocol events into the trigger system would couple triggers to the wire format and make the conditional-guard seam hard to define.

## Decision

Each provider runtime projects its raw protocol into a typed
`ProviderEventEnvelope`; this provider-adapter boundary is the single raw-protocol
translation point. The reactor consumes the merged provider stream, resolves
provider identity to the Dispatch-local lane key, persists/reduces the envelope,
and publishes the typed internal `LaneEvent` union. Triggers and the
conditional-guard seam still operate **only** on `LaneEvent`s, never on raw
protocol dicts or raw hook payloads. Codex's client/router is its provider
projector; Claude's projector aggregates owned stream and hook observations
before it emits an envelope.

Initial `LaneEvent` vocabulary (extend as needed):

- `TurnStarted`, `TurnCompleted`, `TurnFailed`
- `LaneIdle` (derived from status → idle)
- `ApprovalRequested` (command / file-change)
- `ItemCompleted`, `DiffUpdated`
- `StatusChanged`, `TokenUsageUpdated`

Each carries the Dispatch-local lane key, turn id where applicable, and a typed
payload. Provider identity remains available on the persisted envelope and may be
included in typed payloads where consumers need it.

## Assumptions

- This vocabulary covers v1 trigger needs (`idle_for`, `turn_completed`,
  `waiting_on_approval`); new event types are added at the provider-adapter /
  reactor boundary as needs appear.
- Derived events (e.g. `LaneIdle`) are computed once in the provider-neutral
  reactor/reducer, not re-derived per consumer.

## Consequences

- Triggers stay stable across provider protocol drift; only that provider's
  projector changes.
- Guards and (future) conditional triggers are expressed against a clean, typed model.

## Alternatives considered

- **Operate on raw protocol notifications** — rejected: brittle, leaks the wire format into triggers, hard to guard against.

## References

- ADR-0003 (Own Scheduler); `.claude/rules/client.md`; `docs/research/app-server-verification.md` (notification grammar).
