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
| 0005 | Full Read/Write on Existing Threads with Idle-Only Default Guard | Proposed — pending Phase-1 cross-process spike |
