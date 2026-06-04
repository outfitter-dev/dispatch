---
id: 0010
slug: surface-projections-are-ergonomic-not-isomorphic
title: Surface Projections Are Ergonomic, Not Isomorphic
status: proposed
created: 2026-06-03
updated: 2026-06-03
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0010: Surface Projections Are Ergonomic, Not Isomorphic

## Context

ADR-0000 says every operation is authored once and every surface is derived from the op registry. The first implementation used a simple projection: one op becomes one CLI command and one MCP tool. That proved the no-drift foundation, but it is not automatically the best user experience for every surface.

Humans using a shell benefit from command-shaped affordances, stable JSON output, and schemas that make `jq` and automation predictable. Agents using MCP benefit from fewer, workflow-shaped tools with strong guidance about when to use `send`, `steer`, `brief`, `show`, triggers, and remote lanes. A parity test that requires MCP tool names to equal op ids would eventually block the more ergonomic Trails-style projection we actually want.

## Decision

The op registry owns semantic operations. Surfaces derive affordances. A surface may group, rename, or compose operations for ergonomics, but it may not restate schemas, examples, safety intent, error behavior, or capability policy.

For the CLI:

- Derive command help from `Op.summary` and Pydantic field descriptions.
- Derive command examples from `Op.examples`.
- Expose output schemas derived from each op's output model so shell users can script with confidence.
- Keep successful machine output stable and JSON-shaped by default.
- Add streaming CLI affordances for naturally live views such as `show --follow`, `log --follow`, and lane/event watch commands once ADR-0008 notifications are implemented.

For MCP:

- Do not require one MCP tool per op forever.
- Prefer grouped, agent-shaped tools when that reduces tool sprawl and improves selection quality.
- Derive each grouped action's argument schema, output schema, intent, idempotence, examples, and error projection from the underlying op contract.
- Include agent guidance in the derived tool descriptions: read/write/destructive intent, retry expectations, lane authority, attached-lane limits, and when to use `send` vs `steer` vs `brief`.

Parity tests must evolve from "MCP tools equal registry ids" to "every op is reachable through a derived projection with matching schema, examples, safety annotations, and error semantics."

## Consequences

### Positive

- Keeps the no-drift contract while letting each surface feel native.
- Makes CLI output friendlier to `jq`, scripts, and documentation generation.
- Reduces MCP tool sprawl before the op set grows large.
- Preserves the Trails principle: author what is new, derive what is known, override what is wrong.

### Tradeoffs

- Surface parity tests become more semantic and less name-based.
- MCP projection needs grouping metadata or policy, not just a flat map.
- CLI streaming depends on the control socket notification work described in ADR-0008.

## Alternatives considered

- **Keep one op = one CLI command = one MCP tool forever** — simple, but it overfits the first implementation and makes MCP less ergonomic as the op set grows.
- **Hand-design MCP tools separately** — rejected: good short-term UX, bad long-term drift.
- **Make grouped MCP tools the canonical operations** — rejected: grouping is a surface affordance, not the semantic contract.

## References

- ADR-0000 (Contract-First, Surface-Derived Design)
- ADR-0008 (Control-Socket Protocol)
- `docs/development/design.md`
