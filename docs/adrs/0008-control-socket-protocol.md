---
id: 0008
slug: control-socket-protocol
title: Control-Socket Protocol — JSON-RPC-lite over JSONL
status: accepted
created: 2026-06-02
updated: 2026-09-11
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

### Execution-bound schema compatibility (DIS-70)

The implemented handshake is the reserved `__dispatch/metadata` method. It
reports the protocol version, package version, supported op IDs and per-op
input/output schema fingerprints. Whole-registry drift is diagnostic; compatibility
is checked for the invoked op.

Protocol version 2 adds the reserved `__dispatch/execute` method:

```json
{
  "id": 1,
  "method": "__dispatch/execute",
  "params": {
    "op": "send",
    "params": {"lane": "@builder", "text": "Continue"},
    "op_schema_hash": "<expected per-op schema fingerprint>"
  }
}
```

The receiving daemon resolves the authored op and checks its own schema before
calling the handler. A mismatch returns typed `daemon_stale` with exit code 8.
Malformed envelopes fail before execution. This is a transport control method,
not a second op registry or hand-authored public operation.

CLI and MCP retain preflight diagnostics, but a successful probe on another
connection cannot authorize unchecked execution. An old daemon that does not
understand the reserved method rejects it instead of silently ignoring a new
input field. Provider-bearing and other schema-sensitive operations never fall
back to a raw op after that rejection.

Legacy compatibility is limited to the existing derived read-safe and
baseline-safe op sets. A client must obtain fresh metadata and send an eligible
raw op over that same established socket; if it closes, the client cannot reuse
the decision on a replacement connection. The existing per-op hash and
release-baseline policy still applies. This preserves proven inspection/drain
operations without reopening the daemon replacement race. Version 2 daemons
continue accepting raw ops for older clients; those clients do not gain the new
execution-bound guarantee until upgraded.

Schema validation proves shape compatibility, not provider readiness, caller
authority, idempotent execution or completion. Those remain the authored op's
responsibility. A connection lost after possible submission does not prove that
the handler was never called.

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
