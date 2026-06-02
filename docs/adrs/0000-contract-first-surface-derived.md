---
id: 0000
slug: contract-first-surface-derived
title: Contract-First, Surface-Derived Design
status: accepted
created: 2026-06-02
updated: 2026-06-02
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0000: Contract-First, Surface-Derived Design

## Context

dispatch must expose the same operations through a CLI and an MCP server now, and a remote-control surface later. Building each surface by hand is the well-known drift trap: schemas restated, error handling duplicated, parallel behavior paths that diverge over time (the exact pain Trails was built to remove). We want adding or changing an operation to be a single authoring act that updates every surface.

## Decision

Author each operation **once** as an *op* in `contracts/`: input/output Pydantic models, `intent`, `idempotent`, examples, and an async handler. Collect ops in a registry. Every surface is a **pure projection** of that registry — `derive_cli`, `derive_mcp`, `derive_remote` — mirroring Trails' `derive → create → surface` ladder. Surfaces contain projection wiring only; they never hand-implement an op. CLI flags, MCP tool defs/annotations, and error/exit codes are derived from the op. Where a derivation is wrong for one op, override explicitly on the op.

A parity test asserts the ops exposed by every surface equal the registry.

## Consequences

### Positive

- Adding capability = adding one op. Surfaces stay consistent by construction, not vigilance.
- MCP is a v1 surface, not a later bolt-on that re-states everything.
- The boundary is testable: examples-as-tests verify behavior independent of surface.

### Tradeoffs

- A small projection layer to build and maintain before the first op pays off.
- Some surface-specific affordances need explicit override hooks on ops.

## Alternatives considered

- **Hand-write each surface** — rejected; that is the drift we are avoiding.
- **Port Trails to Python** — rejected; too much for dispatch's ~12 ops. Borrow the principle, not the framework.

## References

- `docs/development/design.md`; `.claude/rules/contracts.md`, `.claude/rules/surfaces.md`
- Trails: `why-trails.md`, ADR-0035 (Surface APIs Render the Graph)
