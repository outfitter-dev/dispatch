# surfaces/ — derived surfaces

Path: `src/outfitter/dispatch/surfaces/`. Each surface is a thin, generated projection of the op registry. **Never hand-write per-op surface code.**

## Rules

- A surface module turns `derive_*(registry)` output into a running boundary. It contains projection wiring only — no operation logic (that's in `core/` handlers via `contracts/`).
- **CLI** (`cli.py`): build a Typer app from `derive_cli(registry)`. The command
  tree may group/alias ops for shell ergonomics, but each command marshals
  contract input → calls the daemon control socket → renders the result with
  Rich. The CLI is a **sync** client; it does not import `core/` or `client/`.
  Process/control commands such as `doctor`, `up`, `down`, `registry migrate`,
  `schema`, and `mcp` are the allowed exceptions: they manage or inspect the
  surface/runtime itself and must not duplicate op behavior.
- **MCP** (`mcp.py`): a stdio MCP server (via the `mcp` SDK) from
  `derive_mcp(registry)`; grouped tool handlers route to the daemon control
  socket, same as the CLI. Spawned by the MCP client (Claude/Codex), not hosted
  in the daemon.
- **remote** (later): the daemon control protocol exposed over an authenticated network transport — another projection, no new op authoring.
- Error projection happens here (catch `DispatchError`, map via the taxonomy): CLI → exit code + message; MCP → `isError` + `_meta`; remote → JSON-RPC error.
- Keep the **parity test** green: every registered op must be reachable through
  each surface's derived projection with matching schemas, annotations, and error
  projection. Surface names do not need to equal op ids.
- Destroy-intent CLI routes must preserve the derived confirmation behavior:
  prompt interactively, and require `--yes` when paired with `--no-interactive`.

## Why

This is the whole point of dispatch's architecture and the reason to add an op in one place: adding/changing an operation must never require editing a surface. If you find yourself hand-editing a surface to add behavior for one op, stop — push it into the op contract and its derivation.
