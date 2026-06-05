# contracts/ — the contract layer (Trails-inspired)

Path: `src/outfitter/dispatch/contracts/`. This is the heart of the no-drift design. **Author what's new, derive what's known, override what's wrong.**

## Authoring an op

An **op** is the single source for one operation. Author only what's irreducible:

- `input` — Pydantic model. Fields project to CLI options and the MCP `inputSchema`. Add field descriptions; they become CLI help and MCP schema descriptions.
- `output` — Pydantic model. Projects to CLI rendering and MCP `structuredContent`/`outputSchema`.
- `intent` — `read | write | destroy`. A behavioral assertion surfaces honor (see below).
- `idempotent` — bool → MCP `idempotentHint`.
- `examples` — input + expected output or error class. These ARE the tests (`test_examples`) and the docs.
- `handler` — `async (input, ctx) -> output`. Runs in the daemon. Surface-agnostic: never import CLI/MCP/socket types here. Raise typed `DispatchError`s; do not catch-and-format (surfaces do that).

Register the op in `ops.py`. Then make sure each projection has an intentional
route for it: simple ops may map directly, while ergonomic surfaces may group or
compose ops (for example `list --unmanaged` → `discover`, `goal status` →
`goal-get`, and grouped MCP tools with an `op` selector). The projection must be
derived from the registry; never hand-implement the same behavior separately in a
surface.

## Derivation (never hand-write a surface per op)

Surfaces are pure projections of the registry, mirroring Trails' `derive* → create* → surface`:

- `derive_cli(registry)` → Typer command tree. Command paths may be ergonomic
  aliases/groups over ops, but input/output schemas and error handling still come
  from the op contract. `intent: destroy` → confirm prompt; `read`/`write` → none.
- `derive_mcp(registry)` → grouped MCP tool defs. Tools group related ops by
  workflow/safety boundary and select the op with an `op` argument; per-op
  argument schemas still come from `input.model_json_schema()`, and annotations
  come from `intent`/`idempotent`.
- `derive_remote(registry)` → control-socket method table (and later the network surface).

If a derivation is wrong for a specific op, **override** explicitly on the op — overrides are visible escape hatches, not the default. If you're overriding everywhere, the derivation rule is wrong; fix the rule.

## Errors

One `DispatchError` hierarchy in `errors.py` (e.g. `NotFoundError`, `LaneBusyError`, `ApprovalRequiredError`, `AppServerError`). Each carries the taxonomy needed to project per surface: CLI exit code, MCP `_meta` code + `isError`, remote JSON-RPC code. Keep the projection table in one place so surfaces stay coherent.

## Rules

- Adding capability = adding an op, registering it, and ensuring the derived
  projections route it intentionally. If a route is missing, the parity tests
  should fail.
- Every op exposed on MCP/remote must define `output`.
- Keep handlers pure-ish: input in, output out (or raise). Side effects go through injected dependencies (the App Server client, the registry) passed via `ctx`, never imported ad hoc.
- A parity test must stay green — and it checks **behavior/reachability, not
  identical surface names**. Per op, assert the derived projections agree: CLI
  route/schema ↔ MCP grouped action/input/output schema ↔ the input/output
  models; MCP annotations ↔ `intent`/`idempotent`; error-code projection
  consistent across surfaces. Enumerating that the same op *names* exist on each
  surface is not enough — drift hides in route/schema/annotation/error mapping.
