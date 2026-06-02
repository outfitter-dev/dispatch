---
id: 0009
slug: mcp-daemon-lifecycle
title: MCP Daemon Lifecycle — Auto-Start Detached Singleton
status: proposed
created: 2026-06-02
updated: 2026-06-02
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0009: MCP Daemon Lifecycle — Auto-Start Detached Singleton

> Proposed. The UX is agreed; the singleton-locking + failure-mode details below must be specified (and not hidden) before implementation.

## Context

`dispatch mcp` is a stdio MCP server spawned by the MCP client (Claude/Codex). It routes tool calls to the daemon over the control socket — but the daemon may not be running when an MCP client launches it. We want MCP to "just work," without the daemon being a child of (and dying with) the MCP client, and without hiding startup races.

## Decision

`dispatch mcp` connects to the daemon; if absent, it **auto-starts a detached singleton daemon**, then connects.

- **Detached:** the daemon is not a child of the MCP process. Closing the MCP client never kills lanes.
- **Singleton:** startup takes a lock (pidfile/lockfile + control-socket probe). Concurrent starters race on the lock; the loser waits for the winner's socket. No two daemons.
- **Explicit failure:** if the daemon can't start or the socket doesn't come up within a bounded wait, `dispatch mcp` returns a clear error to the MCP client — it never hangs or silently degrades to a half-working state.

## Assumptions / open items (call out, do not bake silently)

- Singleton locking is reliable across the race (pidfile staleness + socket liveness probe handle crashed-daemon cases). **Mechanism to be specified.**
- First-MCP-connect pays a one-time daemon-startup latency; acceptable.
- **Open:** who reaps an idle daemon (lifetime/GC policy) — deferred past v1.
- The same auto-start path should back `dispatch up` and CLI commands, so there's one start mechanism, not two.

## Consequences

- MCP clients get zero-config startup; lanes survive client churn.
- One daemon-start path shared by CLI and MCP.

## Alternatives considered

- **Require explicit `dispatch up`** — rejected: worse UX for MCP clients.
- **Daemon as child of the MCP process** — rejected: closing the client kills lanes.
- **In-process ephemeral core inside `dispatch mcp`** — rejected: violates the single-daemon model (ADR-0002) and would spawn a second app-server.

## References

- ADR-0002 (Single Daemon), ADR-0008 (Control-Socket Protocol).
