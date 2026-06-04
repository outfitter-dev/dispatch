---
id: 0008
slug: control-socket-protocol
title: Control-Socket Protocol — JSON-RPC-lite over JSONL
status: accepted
created: 2026-06-02
updated: 2026-06-02
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0008: Control-Socket Protocol — JSON-RPC-lite over JSONL

## Context

The CLI, MCP server, and (later) remote surface all talk to the daemon over its Unix socket. That protocol is the canonical surface everything else derives from, so it needs a defined shape — and it must carry not just request/response but **server-push streaming** (live `show`, `log --follow`, roster updates), which a naive request/response design cannot.

## Decision

Use **newline-delimited JSON, JSON-RPC 2.0-lite** — the same family as the Codex App Server (symmetry; we already model that shape):

- Requests: `{id, method, params}`. Responses: `{id, result}` or `{id, error:{code,message,data}}`.
- **Notifications** (no `id`) carry server-push: lane events, diff/output deltas, status — keyed by a subscription/lane id the client opened.
- **Versioning:** an `initialize`/hello exchange carries a protocol version + capabilities; mismatches fail loudly at connect.
- **Errors** project the `DispatchError` taxonomy (ADR-0001) into JSON-RPC error codes.

`derive_remote` later reuses this exact protocol; the network surface adds only transport + auth.

## Assumptions (framing/versioning spec to lock during Phase-2)

- JSONL over the Unix socket is sufficient — no length-prefix/binary framing needed (the App Server proves JSONL at this scale).
- One message per line; messages are small (deltas stream as many notifications, not giant payloads).
- Subscriptions are explicit (a client opts into a lane's event stream), so the daemon can scope and clean up pushes.

## Consequences

- Streaming surfaces (`show`, `log --follow`) work natively via notifications.
- Reusing the App-Server-shaped protocol means shared patterns/models and a trivial `derive_remote`.

## Alternatives considered

- **Request/response only** — rejected: cannot stream live lane events.
- **Length-prefixed / binary framing** — rejected: more complexity than JSONL needs here.
- **HTTP/gRPC for the local socket** — rejected: heavyweight for a single-host control plane.

## References

- ADR-0001 (error taxonomy), ADR-0002 (daemon), ADR-0007 (LaneEvent); App Server JSON-RPC-lite (`docs/research/app-server-verification.md`).
