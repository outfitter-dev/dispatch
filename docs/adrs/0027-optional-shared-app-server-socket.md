---
id: 0027
slug: optional-shared-app-server-socket
title: Optional Shared App Server Socket Attachment
status: proposed
created: 2026-08-26
updated: 2026-08-26
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0027: Optional Shared App Server Socket Attachment

## Context

Dispatch historically spawned and owned one `codex app-server --listen stdio://`
process. Codex Desktop normally spawns a separate stdio App Server, so Dispatch and
Desktop can see the same persisted threads through `CODEX_HOME` but do not share live
event fan-out or a cross-process write interlock.

Current Codex App Server builds can instead listen on a local Unix socket using
WebSocket framing. An isolated probe against Codex 0.149 verified that multiple clients
can initialize through that socket, `codex app-server proxy` reaches the same endpoint,
and disconnecting one client does not stop the server. The current Desktop bundle also
contains an undocumented launch gate that can connect Desktop to a compatible managed
local daemon, but that Desktop behavior is not a supported Dispatch contract.

## Decision

Keep owned stdio as the default. Add one explicit opt-in endpoint:

- `DISPATCH_APP_SERVER_SOCKET=/absolute/path.sock` for a one-shot process environment.
- `[app_server].socket_path` in `~/.dispatch/config.toml` for durable Dispatch endpoint
  selection.

When a socket is configured, Dispatch connects with WebSocket-over-Unix and owns only
its client connection. It must not start, stop, restart, update, or unlink the shared App
Server. A missing, incompatible, or disconnected socket fails loudly; Dispatch does not
silently fall back to spawning stdio.

Lane authority does not change. Attached lanes remain turn-write locked by default under
ADR-0005. Shared-process co-presence is a transport improvement, not proof that Desktop
and Dispatch can safely issue concurrent writes to one thread.

## Consequences

### Positive

- Desktop and Dispatch can be tested against one App Server process without
  reverse-engineering Desktop IPC.
- Live notifications can fan out within one App Server process.
- The default installation and lifecycle remain unchanged.
- `dispatch down` cannot terminate a server it does not own.

### Tradeoffs

- Unix transport adds a WebSocket dependency and inherits platform socket-path limits.
- Operators own daemon readiness and launch ordering before starting Dispatch.
- Desktop's local-daemon launch gate is undocumented and may change independently.
- A shared process still needs an explicit writer-ownership or handoff protocol before
  attached writes can be considered safe by default.

## Alternatives considered

- **Replace stdio with the managed daemon for everyone** — rejected because it changes
  installation, update, and reboot behavior for existing users.
- **Use `codex app-server proxy` as a JSONL subprocess** — rejected because proxy is a raw
  byte bridge carrying the WebSocket handshake and frames; it does not translate JSONL.
- **Attach through ChatGPT's `ipc.sock`** — rejected because that is undocumented Desktop
  owner/follower IPC, not the App Server protocol.
- **Silently fall back to stdio** — rejected because it would recreate the two-server
  topology while appearing to run in shared mode.

## References

- [ADR-0002](0002-single-daemon-over-one-app-server.md)
- [ADR-0005](0005-lane-authority-capability-ladder.md)
- [`docs/research/app-server-verification.md`](../research/app-server-verification.md)
- [Codex App Server protocol and transports](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
