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

Register the op in `registry.py`. That is the whole authoring step — it now appears on every surface.

## Derivation (never hand-write a surface per op)

Surfaces are pure projections of the registry, mirroring Trails' `derive* → create* → surface`:

- `derive_cli(registry)` → Typer commands. `intent: destroy` → confirm prompt; `read`/`write` → none.
- `derive_mcp(registry)` → MCP tool defs. Tool name from op id; schema from `input.model_json_schema()`; annotations from `intent`/`idempotent`.
- `derive_remote(registry)` → control-socket method table (and later the network surface).

If a derivation is wrong for a specific op, **override** explicitly on the op — overrides are visible escape hatches, not the default. If you're overriding everywhere, the derivation rule is wrong; fix the rule.

## Errors

One `DispatchError` hierarchy in `errors.py` (e.g. `NotFoundError`, `LaneBusyError`, `ApprovalRequiredError`, `AppServerError`). Each carries the taxonomy needed to project per surface: CLI exit code, MCP `_meta` code + `isError`, remote JSON-RPC code. Keep the projection table in one place so surfaces stay coherent.

## Rules

- Adding capability = adding an op + registering it. Nothing else.
- Every op exposed on MCP/remote must define `output`.
- Keep handlers pure-ish: input in, output out (or raise). Side effects go through injected dependencies (the App Server client, the registry) passed via `ctx`, never imported ad hoc.
- A parity test must stay green — and it checks **behavior, not just op names**. Per op, assert the derived projections agree: CLI option set ↔ MCP `inputSchema`/`outputSchema` ↔ the input/output models; MCP annotations ↔ `intent`/`idempotent`; error-code projection consistent across surfaces. Enumerating that the same op *names* exist on each surface is not enough — drift hides in the per-op schema/annotation/error mapping.
