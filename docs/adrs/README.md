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
| [0005](0005-lane-authority-capability-ladder.md) | Lane Authority Capability Ladder | Accepted — Phase-1 spike keeps attached lanes turn-write locked |
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
| [0018](0018-top-level-thread-actions-and-search.md) | Top-Level Thread Actions and Search | Accepted |
| [0019](0019-dispatch-local-refs-and-flat-thread-cli.md) | Dispatch-Local Refs and Flat Thread CLI | Accepted |
| [0020](0020-live-use-trust-contracts.md) | Live-Use Trust Contracts | Accepted |
| [0021](0021-lane-inbox-and-delivery.md) | Lane Inbox and Delivery | Proposed |
| [0022](0022-event-subscriptions.md) | Event Subscriptions | Proposed |
| [0023](0023-provider-event-log-and-history-index.md) | Provider Event Log and History Index | Proposed |
| [0024](0024-provider-thread-topology-is-independent-of-lane-authority.md) | Provider Thread Topology Is Independent of Lane Authority | Accepted |
| [0025](0025-provider-capacity-observations-are-latest-value-snapshots.md) | Provider Capacity Observations Are Latest-Value Snapshots | Accepted |
| [0026](0026-claude-control-uses-resume-processes-and-hooks.md) | Claude Control Uses Resume Processes and Hooks | Proposed |
