---
id: 0013
slug: dispatch-mesh-is-daemon-federation
title: Dispatch Mesh Is Daemon Federation
status: proposed
created: 2026-06-03
updated: 2026-09-11
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0013: Dispatch Mesh Is Daemon Federation

## Context

The long-term dream for dispatch is multi-machine coordination: two machines, possibly running different Codex accounts and different local workspaces, should be able to coordinate agents. A coordinator on one machine should address a lane on another, send it work, receive status/events, and react through triggers.

Codex App Server is local to a machine/account/runtime. Trying to share `~/.codex`, a live app-server connection, or a Codex thread across machines would blur authority and create unsafe assumptions about filesystem access, approvals, account identity, and concurrent writers.

## Decision

The mesh federates dispatch daemons, not Codex App Servers.

Each station runs its own `dispatchd` and owns its local provider bindings, accounts, filesystem authority, approvals, registry and lane policy. A station is a runtime boundary; a host may contain isolated stations. The network exchanges Dispatch-level envelopes and bounded selected state. [ADR-0028](0028-stations-own-provider-bindings-and-durable-execution.md) defines the shared station/provider identity and execution foundation; this remains proposed network behavior.

Remote lanes are addressable but not local:

- Local handles remain local.
- Existing `@project:name` handle syntax keeps its meaning. A separate station selector qualifies a stable station-local Dispatch thread key; exact CLI grammar remains a contract decision.
- The local daemon routes remote ops to the owning peer daemon.
- The remote daemon authorizes and executes the op locally.
- Targets pin network, station, station incarnation and Dispatch thread identity. Provider binding/native session resolution stays within the station.
- Remote events are relayed as normalized dispatch events, not raw app-server streams.

The mesh must preserve local sovereignty: no remote peer receives implicit access to another machine's filesystem, shell, app-server, or account. Remote command execution means "invoke an authorized dispatch op remotely," not arbitrary shell by default.

## Consequences

### Positive

- Supports different Codex accounts naturally.
- Keeps filesystem, approvals, and app-server authority local.
- Lets agents coordinate across machines through the same op/trigger model.
- Avoids pretending one Codex lane can safely exist in two runtimes.

### Tradeoffs

- Remote lanes have latency, failure, and partial-connectivity semantics.
- Event streams need cursors/summaries rather than assuming live in-process fan-out.
- Some local affordances (`steer`, `interrupt`, raw output tails) may be gated or unavailable remotely until policy and transport support them.

## Alternatives considered

- **Share one App Server across machines** — rejected: wrong authority boundary and not how Codex state/runtime works.
- **Sync `~/.codex` between machines** — rejected: unsafe, racy, and account-confusing.
- **Treat remote machines as SSH shells only** — rejected: useful for administration, but it bypasses dispatch's op contracts, safety policy, and audit trail.

## References

- ADR-0002 (Single Daemon over One App Server)
- ADR-0005 (Lane Authority Capability Ladder)
- ADR-0010 (Surface Projections Are Ergonomic, Not Isomorphic)
