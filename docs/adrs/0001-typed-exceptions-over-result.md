---
id: 0001
slug: typed-exceptions-over-result
title: Typed Exceptions over a Result Type
status: accepted
created: 2026-06-02
updated: 2026-06-02
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0001: Typed Exceptions over a Result Type

## Context

Trails mandates `Result<T, Error>` — blazes never throw — which gives a single, transport-independent error path that each surface projects (exit code, MCP `_meta`, HTTP status). We want the same property: one error taxonomy, projected per surface. But dispatch is Python, where exceptions are the idiom and a pervasive `Result` type (via a library or hand-rolled `Ok`/`Err`) reads as un-Pythonic ceremony at every call site.

## Decision

Keep the *taxonomy* discipline; drop the *return shape*. Handlers **raise** typed `DispatchError` subclasses (`NotFoundError`, `LaneBusyError`, `ApprovalRequiredError`, `AppServerError`, …). Handlers do not catch-and-format. Each **surface** catches `DispatchError` at its boundary and projects it via a single taxonomy table: CLI → exit code + Rich message; MCP → `isError` + `_meta` code; remote → JSON-RPC error. Unknown/native exceptions project to a generic internal error.

This is the deliberate, idiomatic-Python divergence from Trails: same single-source error behavior, native control flow.

## Consequences

### Positive

- Idiomatic Python; no `Result` noise in handler/business code.
- One taxonomy → many surface projections (the property we wanted).

### Tradeoffs

- The "never throw" compile-time guarantee is replaced by convention + review (handlers raise typed errors; surfaces are the only catchers). mypy and tests guard it, not the type system.

## Alternatives considered

- **`returns`-library `Result`** — faithful to Trails, but verbose and against Python norms.
- **Hand-rolled `Ok`/`Err`** — same downside, plus a bespoke type to maintain.

## References

- `docs/development/design.md`; `.claude/rules/python-conventions.md`, `.claude/rules/contracts.md`
- Trails ADR-0002 (Built-In Result Type), ADR-0026 (Error Taxonomy as Transport-Independent Behavior Contract)
