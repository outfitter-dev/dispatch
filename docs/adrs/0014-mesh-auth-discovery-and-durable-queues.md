---
id: 0014
slug: mesh-auth-discovery-and-durable-queues
title: Mesh Auth, Discovery, and Durable Queues
status: proposed
created: 2026-06-03
updated: 2026-09-11
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0014: Mesh Auth, Discovery, and Durable Queues

## Context

Daemon federation needs more than a socket. Machines sleep, move networks, lose connectivity, and need durable work even when sender and destination are not online together. Private station connectivity uses Tailscale; cloud MCP callers need authenticated gateway access and a durable mailbox. SSH is not the Dispatch application transport. [ADR-0028](0028-stations-own-provider-bindings-and-durable-execution.md) records the shared local identity, admission and evidence foundation.

Discovery is not authorization. Reachability over Tailscale, mDNS or LAN answers "can I find this machine?" It does not answer "what may this caller do?"

## Decision

Use one immutable operation envelope and local reservation path across two explicit routes:

- **Gateway:** authenticated cloud MCP admission, durable mailbox, outbound station collection and compact receipt/result access.
- **Direct:** private HTTPS over Tailscale with a durable sender outbox, station identity and explicit caller/delegation authority. Both peers must eventually be online together.

Station enrollment and authority:

- Auto-discovery may use Tailnet/MagicDNS, mDNS where appropriate, or configured addresses.
- Pairing exchanges stable device public keys and establishes a dispatch-level trust record.
- Transport identity helps, but dispatch capabilities decide authorization.
- Final authorization stays with the destination station and is rechecked before execution. Tailnet reachability alone grants no Dispatch capability.

Gateway trust and bounded grants:

- Remote commands invoke dispatch ops, not arbitrary shell.
- Pairing and caller grants require explicit allowed operations, target scope and expiry. Exact credential/delegation mechanics are a proof and design gate, not implemented by this ADR.
- A gateway processing plaintext MCP arguments is a trusted processor of those selected payloads, not an end-to-end confidential relay. Record the explicit trust decision before writes; requiring application-level encryption instead needs client-compatible protocol proof.
- Keep provider credentials, raw history, native events and full tool output local by default. Selected labels/summaries also require publication policy.

All remote delivery uses durable queues:

- Outbound envelopes pin network operation identity, authenticated principal/key scope, target station/incarnation/thread, canonical op/input, request digest and work expiry.
- Destination receipt mapping is atomic locally and deduped within authenticated scope. Gateway admission, station reservation, provider acceptance and execution remain separate facts.
- Replies and event relays use cursors or acknowledgements so reconnects can resume without duplicate side effects.
- Redelivery returns the same local reservation. A possibly submitted provider call remains uncertain until reconciled; transport retry never authorizes a new execution. No exactly-once guarantee or automatic route fallback is implied.

## Consequences

### Positive

- Survives laptop sleep, network drops, and temporarily offline peers.
- Supports both durable mesh collaboration and one-off remote reach.
- Keeps Tailnet discovery convenient without making it the authorization model.
- Makes remote op retry safe enough to automate.

### Tradeoffs

- Requires a local queue schema and retry supervisor before remote ops can be reliable.
- Pairing and capability UX must be clear or users will distrust the mesh.
- Gateway trust, revocation freshness, credential rotation, retention and restore fences require explicit decisions and tests before remote writes.

## Alternatives considered

- **Live-only remote calls** — rejected: too fragile for multi-machine agent work.
- **Tailnet identity alone is authorization** — rejected: reachability and authorization are separate concerns.
- **Permanent peering required for all remote commands** — rejected: too much ceremony for one-off work.
- **Ad hoc shell over SSH as the remote surface** — rejected: useful escape hatch, but not the dispatch protocol.

## References

- ADR-0008 (Control-Socket Protocol)
- ADR-0013 (Dispatch Mesh Is Daemon Federation)
