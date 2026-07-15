---
id: 0002
slug: single-daemon-over-one-app-server
title: Single Daemon over One App Server
status: accepted
created: 2026-06-02
updated: 2026-07-15
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0002: Single Daemon over One App Server

## Context

dispatch drives many lanes (Codex threads), reacts to their live events for triggers, and must answer surfaces (CLI/MCP). Event-driven triggers require a continuous subscription, which implies a long-lived process. The App Server exposes lanes over a transport; only **stdio is bare newline-delimited JSON** (verified) — `unix://`/`ws://` are WebSocket-framed and the managed daemon's control socket is auth-gated. One App Server process can host many threads, and a single stdio connection can multiplex them (verified: persisted-thread resume fans out live events to a connection).

## Decision

Run one long-lived **daemon** (`dispatchd`) that spawns and owns **one** `codex app-server --listen stdio://` (sharing `CODEX_HOME=~/.codex` so it sees existing desktop lanes). A message router demuxes the single connection by request id / `threadId` into per-lane event streams. The daemon hosts the core and executes all op handlers, and exposes a Unix-socket control API — the canonical projection surfaces render. The CLI is a thin **sync** client; MCP (`dispatch mcp`) is a stdio server routing to the same control API.

We drive the App Server binary directly; the `openai-codex` SDK has lagged the installed CLI before, so adopting it would require a fresh bundled-binary check.

This decision governs the Codex runtime, not the number of execution providers.
`dispatchd` remains the single operation/control authority, while a fixed
provider manager may also own provider-specific runtimes such as the Claude
resume-process supervisor in ADR-0026. Claude sessions never go through or spawn
another Codex App Server.

## Consequences

### Positive

- Clean event multiplexing over one connection (the verified pattern); one place owns App Server lifecycle.
- Surfaces are thin clients of one control API; no surface re-implements orchestration.

### Tradeoffs

- A process to keep alive (launchd) and supervise (restart + restore lane observation on app-server crash).
- A second App Server alongside the desktop app shares `~/.codex` — cross-process safety on a shared thread is unverified (see ADR-0005 / Phase-1 spike).

## Alternatives considered

- **Spawn-per-client (like the SDK)** — no shared event bus; can't do cross-lane triggers cleanly.
- **Attach to the running managed daemon** — its control socket handshake is undocumented/auth-gated; not externally drivable today.
- **WebSocket/unix transport** — needs a WS client and (off-loopback) auth; stdio is simpler and sufficient.

## References

- `docs/development/design.md`; `docs/research/app-server-verification.md`; `.claude/rules/client.md`; [ADR-0026](0026-claude-control-uses-resume-processes-and-hooks.md)
