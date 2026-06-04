---
id: 0012
slug: conditional-triggers-and-event-sinks
title: Conditional Triggers and Event Sinks
status: proposed
created: 2026-06-03
updated: 2026-06-03
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0012: Conditional Triggers and Event Sinks

## Context

ADR-0003 gives dispatch its own scheduler and reactor. The v1 trigger model supports simple time/event triggers and actions like `send`, `steer`, and `brief`. The guard layer is intentionally small (`idle_only`, `min_interval`, `dedupe`) but was designed as the seam for future conditional triggers.

A natural next capability is an "externalizer hook" pattern: watch normalized lane events, keep state across related events, and emit structured logs, event records, or controlled commands when conditions match. This should not leak raw Codex App Server JSON into trigger definitions, and it should not immediately become arbitrary shell execution.

## Decision

Extend triggers toward declarative conditional event sinks:

- Conditions operate on normalized `LaneEvent` fields, not raw App Server messages.
- The first condition language is structured and bounded: field equality, contains, regex where justified, event type, lane selector, and simple state windows.
- The first external actions are safe sinks: audit log, JSONL event log, and dispatch lane messages.
- Shell/command actions are deferred behind a separate execution-policy decision.
- Every firing is audited with trigger id, matched event, action kind, outcome, and enough detail to debug without storing secrets.

The feature should grow in this order:

1. Broaden normalized event vocabulary only where a concrete trigger needs it.
2. Add structured conditions.
3. Add safe event sinks.
4. Consider controlled command execution only after policy, capability, and audit requirements are explicit.

## Consequences

### Positive

- Enables externalizer-style workflows without coupling users to raw protocol drift.
- Reuses the existing scheduler/reactor/trigger architecture.
- Keeps the safe version useful before introducing command-execution risk.

### Tradeoffs

- A condition language is another contract surface and must be versioned carefully.
- Event payloads need enough normalized detail to be useful without becoming raw-message passthroughs.
- Durable stateful conditions increase registry complexity.

## Alternatives considered

- **Arbitrary Python or shell hooks from the start** — rejected: powerful but too risky for v1 and hard to audit safely.
- **Only fixed trigger types forever** — rejected: too limiting for coordination and externalizer workflows.
- **Expose raw App Server notifications to trigger predicates** — rejected: brittle and contrary to ADR-0007.

## References

- ADR-0003 (Own Scheduler, Not Codex Automations)
- ADR-0007 (Normalized Internal LaneEvent Vocabulary)
- ADR-0008 (Control-Socket Protocol)
