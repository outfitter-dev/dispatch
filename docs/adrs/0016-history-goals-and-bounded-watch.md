---
id: 0016
slug: history-goals-and-bounded-watch
title: History, Goals, and Bounded Watch
status: accepted
created: 2026-06-04
updated: 2026-06-04
owners: ['Dispatch maintainers']
---

# ADR-0016: History, Goals, and Bounded Watch

## Context

The Codex App Server exposes more than the lane messaging primitives dispatch used in v0:
persisted turn snapshots via `thread/read(includeTurns:true)`, native goals via
`thread/goal/{get,set,clear}`, and history controls via `thread/fork`,
`thread/rollback`, and `thread/compact/start`.

It also emits rich live notifications, but dispatch's control socket is currently
request/response JSONL. A true infinite `tail` or push subscription would require a
control-socket protocol extension rather than another ordinary op.

## Decision

Expose the stable App Server primitives through derived ops:

- `transcript` reads persisted turn items using `thread/read(includeTurns:true)`.
- `watch` returns a bounded raw event sample using `limit` and `timeout`.
- `goal-get`, `goal-set`, and `goal-clear` project native App Server goals.
- `fork`, `rollback`, and `compact` project stable history-control methods.

Keep `show` as the compact lane summary, with optional transcript inclusion for
convenience, but document `transcript` as the explicit history operation.

Document that App Server goals and `includeTurns` history reads require non-ephemeral
threads.

Treat `watch` as a bounded sample, not a streaming subscription. A durable live tail
belongs in a later protocol change that can push events over the control socket.

Keep mutating history/goal operations locked to owned lanes. Attached lanes remain
observe-only until cross-process semantics are verified.

## Consequences

### Positive

- Agents can harvest history, inspect goals, and control long-running lanes without
  leaving the contract-derived CLI/MCP architecture.
- The implementation uses stable App Server methods and avoids experimental
  `thread/turns/list` or `thread/search`.
- The watch surface is honest about current transport limits.

### Negative

- `transcript` is a compact persisted snapshot, not a full execution log.
- `watch` is useful for live validation and short samples, but not for dashboards.
- `rollback` does not undo file changes; callers must use Git or another workspace
  mechanism for file-level rollback.

## Alternatives Considered

- **Turn `show` into a transcript viewer** — rejected: it blurs metadata and history,
  and existing operator docs made `show` the summary command.
- **Expose a fake infinite `tail` over request/response JSONL** — rejected: it would
  be misleading and fragile.
- **Use experimental `thread/turns/list` and `thread/search` now** — rejected: stable
  `thread/read(includeTurns:true)` is enough for the first history surface.
