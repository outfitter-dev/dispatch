---
id: 0006
slug: handler-context-and-di
title: Handler Context and Dependency Injection
status: accepted
created: 2026-06-02
updated: 2026-06-02
owners: ['Dispatch maintainers']
---

# ADR-0006: Handler Context and Dependency Injection

## Context

Every op handler needs the App Server client and the registry, must stay surface-agnostic (no CLI/MCP/socket types), and must be testable without standing up a real daemon. Importing infrastructure inline (singletons, module-level clients) couples handlers to concrete infra and makes testing painful — the problem Trails solves with `resource()`/`db.from(ctx)`. We want the same testability without that machinery.

## Decision

Inject a small `Ctx` into every handler. Handlers are `async def handler(input, ctx) -> Output`. `Ctx` carries exactly:

- `client` — the App Server client facade (lane operations).
- `registry` — the lane/trigger store.
- `log` — a structlog logger bound with lane/op context.
- `abort` — a cancellation signal (`asyncio.Event`/token) propagated from the surface.

Handlers never import infrastructure directly. Tests construct a `Ctx` with a fake/mock client and a temp-dir registry — no daemon, no real app-server. This is the testability backbone; it must exist before Phase 2 ops are written.

## Assumptions

- `Ctx` stays small and stable; surface-specific concerns (argv, MCP session, socket) never leak onto it.
- A mock `client` faithfully models the App Server primitives the handler uses.

## Consequences

- Handlers are unit-testable in isolation; integration tests swap in the real client.
- Keeps handlers pure-ish (input + ctx in, output or raise out) and surface-neutral.

## Alternatives considered

- **Global singletons / module-level client** — rejected: untestable, hidden coupling.
- **Trails-style `resource()` capability graph** — rejected: more framework than ~12 ops need; a fixed `Ctx` is enough.

## References

- `.claude/rules/contracts.md`, `.claude/rules/python-conventions.md`; ADR-0000.
