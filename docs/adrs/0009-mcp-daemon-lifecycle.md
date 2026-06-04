---
id: 0009
slug: mcp-daemon-lifecycle
title: MCP Daemon Lifecycle — Auto-Start Detached Singleton
status: accepted
created: 2026-06-02
updated: 2026-06-03
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0009: MCP Daemon Lifecycle — Auto-Start Detached Singleton

> Accepted (2026-06-03) after Phase 5 implemented the detached singleton lifecycle, bounded MCP daemon startup, stale-pid protection, and supervisor restart path. Idle daemon reaping remains deferred.

## Context

`dispatch mcp` is a stdio MCP server spawned by the MCP client (Claude/Codex). It routes tool calls to the daemon over the control socket — but the daemon may not be running when an MCP client launches it. We want MCP to "just work," without the daemon being a child of (and dying with) the MCP client, and without hiding startup races.

## Decision

`dispatch mcp` connects to the daemon; if absent, it **auto-starts a detached singleton daemon**, then connects.

- **Detached:** the daemon is not a child of the MCP process. Closing the MCP client never kills lanes.
- **Singleton:** startup takes a lock (pidfile/lockfile + control-socket probe). Concurrent starters race on the lock; the loser waits for the winner's socket. No two daemons.
- **Explicit failure:** if the daemon can't start or the socket doesn't come up within a bounded wait, `dispatch mcp` returns a clear error to the MCP client — it never hangs or silently degrades to a half-working state.

## Assumptions / open items (call out, do not bake silently)

- Singleton locking is guarded by pidfile/socket liveness probes. Stale pidfiles are not trusted for stop; the daemon must answer on the socket before it is signaled.
- First-MCP-connect pays a one-time daemon-startup latency; acceptable.
- **Open:** who reaps an idle daemon (lifetime/GC policy) — deferred past v1.
- The same auto-start path should back `dispatch up` and CLI commands, so there's one start mechanism, not two.

## Implementation outcome

Phase 5 implemented:

- `dispatch up` / `dispatch down` as a detached singleton lifecycle.
- MCP startup that connects through the same control socket path and fails with bounded timeouts.
- Stale pidfile safety: `down` only signals a pid after a live socket probe confirms a daemon is answering.
- Supervisor restart/re-resume of persisted lanes after app-server EOF.
- A launchd plist generator; actual `launchctl` installation remains a deliberate user action.

## Consequences

- MCP clients get zero-config startup; lanes survive client churn.
- One daemon-start path shared by CLI and MCP.

## Alternatives considered

- **Require explicit `dispatch up`** — rejected: worse UX for MCP clients.
- **Daemon as child of the MCP process** — rejected: closing the client kills lanes.
- **In-process ephemeral core inside `dispatch mcp`** — rejected: violates the single-daemon model (ADR-0002) and would spawn a second app-server.

## References

- ADR-0002 (Single Daemon), ADR-0008 (Control-Socket Protocol).
