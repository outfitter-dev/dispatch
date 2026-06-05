# Architecture Decision Records

ADRs record the significant decisions behind **dispatch** — the choices that, if reversed, would produce a different tool. Each captures context, the decision, consequences, and alternatives considered. Practice inspired by [Trails' ADRs](https://github.com/outfitter-dev/trails/tree/main/docs/adr).

Files are `NNNN-slug.md`. Copy [`template.md`](template.md) to start one. Keep them tight and decision-focused, not tutorials.

## Index

| ADR | Title | Status |
| --- | --- | --- |
| [0000](0000-contract-first-surface-derived.md) | Contract-First, Surface-Derived Design | Accepted |
| [0001](0001-typed-exceptions-over-result.md) | Typed Exceptions over a Result Type | Accepted |
| [0002](0002-single-daemon-over-one-app-server.md) | Single Daemon over One App Server | Accepted |
| [0003](0003-own-scheduler-not-codex-automations.md) | Own Scheduler, Not Codex Automations | Accepted |
| 0004 | Single-Sourced Agent Docs (`.claude/rules` ↔ `AGENTS.md` symlinks) | Accepted — see [`.claude/rules/agent-docs.md`](../../.claude/rules/agent-docs.md) |
| [0005](0005-lane-authority-capability-ladder.md) | Lane Authority Capability Ladder | Accepted — Phase-1 spike confirmed attached=observe-only |
| [0006](0006-handler-context-and-di.md) | Handler Context and Dependency Injection | Accepted |
| [0007](0007-normalized-internal-lane-events.md) | Normalized Internal LaneEvent Vocabulary | Accepted |
| [0008](0008-control-socket-protocol.md) | Control-Socket Protocol — JSON-RPC-lite over JSONL | Accepted |
| [0009](0009-mcp-daemon-lifecycle.md) | MCP Daemon Lifecycle — Auto-Start Detached Singleton | Accepted |
| [0010](0010-surface-projections-are-ergonomic-not-isomorphic.md) | Surface Projections Are Ergonomic, Not Isomorphic | Proposed |
| [0011](0011-codex-session-registration-is-explicit.md) | Codex Session Registration Is Explicit | Proposed |
| [0012](0012-conditional-triggers-and-event-sinks.md) | Conditional Triggers and Event Sinks | Proposed |
| [0013](0013-dispatch-mesh-is-daemon-federation.md) | Dispatch Mesh Is Daemon Federation | Proposed |
| [0014](0014-mesh-auth-discovery-and-durable-queues.md) | Mesh Auth, Discovery, and Durable Queues | Proposed |
| [0015](0015-new-command-config-presets-and-name-prefixes.md) | New Command, Config Presets, and Name Prefixes | Proposed |
| [0016](0016-history-goals-and-bounded-watch.md) | History, Goals, and Bounded Watch | Accepted |
| [0017](0017-progressive-thread-sync-index.md) | Progressive Thread Sync Index | Accepted |
