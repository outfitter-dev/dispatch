---
id: 0014
slug: mesh-auth-discovery-and-durable-queues
title: Mesh Auth, Discovery, and Durable Queues
status: proposed
created: 2026-06-03
updated: 2026-06-03
owners: ['Dispatch maintainers']
---

# ADR-0014: Mesh Auth, Discovery, and Durable Queues

## Context

Daemon federation needs more than a socket. Machines sleep, move networks, lose connectivity, and may only be reachable over Tailnet or SSH. Some relationships are permanent peers; others are one-off remote commands. Users should be able to discover trusted Tailnet candidates, pair them with an end-to-end encrypted pipe, and also send an ad hoc remote dispatch op when they have an SSH path or a short-lived code.

Discovery is not authorization. Reachability over Tailscale, mDNS, LAN, or SSH answers "can I find this machine?" It does not answer "what can it do?"

## Decision

Use one remote envelope protocol with two modes:

- **Peered mode:** durable device identity, pairing, stored capabilities, event cursors, and persistent outbox/inbox queues.
- **Ad hoc mode:** short-lived capability via SSH, pairing code, or another explicit authorization mechanism, without creating a permanent peer.

Peered mode:

- Auto-discovery may use Tailnet/MagicDNS, mDNS where appropriate, or configured addresses.
- Pairing exchanges stable device public keys and establishes a dispatch-level trust record.
- Transport identity helps, but dispatch capabilities decide authorization.
- Payloads should be end-to-end encrypted at the dispatch layer when using a relay or untrusted transport.

Ad hoc mode:

- Remote commands invoke dispatch ops, not arbitrary shell.
- SSH can be a trust path by running a remote `dispatch` receiver under the user's SSH identity.
- Pairing codes are short-lived capability tokens with explicit allowed ops, lane scope, and expiration.

All remote delivery uses durable queues:

- Outbound envelopes have `message_id`, `idempotency_key`, peer/target, op, payload, capability, attempt state, timestamps, and expiration.
- Inbound envelopes are deduped by idempotency key and audited before/after execution.
- Replies and event relays use cursors or acknowledgements so reconnects can resume without duplicate side effects.

## Consequences

### Positive

- Survives laptop sleep, network drops, and temporarily offline peers.
- Supports both durable mesh collaboration and one-off remote reach.
- Keeps Tailnet discovery convenient without making it the authorization model.
- Makes remote op retry safe enough to automate.

### Tradeoffs

- Requires a local queue schema and retry supervisor before remote ops can be reliable.
- Pairing and capability UX must be clear or users will distrust the mesh.
- End-to-end encryption and key rotation add real operational complexity.

## Alternatives considered

- **Live-only remote calls** — rejected: too fragile for multi-machine agent work.
- **Tailnet identity alone is authorization** — rejected: reachability and authorization are separate concerns.
- **Permanent peering required for all remote commands** — rejected: too much ceremony for one-off work.
- **Ad hoc shell over SSH as the remote surface** — rejected: useful escape hatch, but not the dispatch protocol.

## References

- ADR-0008 (Control-Socket Protocol)
- ADR-0013 (Dispatch Mesh Is Daemon Federation)
