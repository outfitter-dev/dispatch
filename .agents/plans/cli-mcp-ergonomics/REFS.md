# CLI/MCP Ergonomics — references

Paths are repo-relative.

## Packet

- `.agents/plans/cli-mcp-ergonomics/PLAN.md` — product grammar, decisions,
  implementation guidance, and verification contract.
- `.agents/plans/cli-mcp-ergonomics/GOAL.md` — pasteable `/goal`.
- `.agents/plans/cli-mcp-ergonomics/RETRO.md` — execution ledger.
- `.agents/plans/PLANNING.md` — repo planning and review conventions.

## Design and ADRs

- `docs/development/design.md` — original architecture and op list.
- `docs/adrs/0000-contract-first-surface-derived.md` — author once, derive
  surfaces.
- `docs/adrs/0010-surface-projections-are-ergonomic-not-isomorphic.md` — CLI
  and MCP projection direction.
- `docs/adrs/0015-new-command-config-presets-and-name-prefixes.md` — `new`,
  config, presets, and name prefixes.
- `docs/adrs/0016-history-goals-and-bounded-watch.md` — transcript, goals,
  watch, fork, rollback, compact.

## Implementation entrypoints

- `src/outfitter/dispatch/contracts/derive_cli.py`
- `src/outfitter/dispatch/contracts/derive_mcp.py`
- `src/outfitter/dispatch/core/ops.py`
- `src/outfitter/dispatch/core/models.py`
- `src/outfitter/dispatch/core/handlers.py`
- `src/outfitter/dispatch/core/trigger_handlers.py`
- `src/outfitter/dispatch/registry/store.py`
- `src/outfitter/dispatch/surfaces/cli.py`
- `src/outfitter/dispatch/surfaces/mcp.py`

## Tests to inspect first

- `tests/surfaces/test_derive_cli.py`
- `tests/surfaces/test_derive_mcp.py`
- `tests/surfaces/test_mcp_routing.py`
- `tests/surfaces/test_parity.py`
- `tests/core/test_handlers.py`
- `tests/core/test_examples.py`
- `tests/core/test_new_config.py`
- `tests/core/test_trigger_handlers.py`

## Operator docs and skills

- `README.md`
- `docs/usage/README.md`
- `skills/dispatch/SKILL.md`
- `skills/dm/SKILL.md`
- `plugins/dispatch/`

## Commands

```bash
uv run dispatch --help
uv run dispatch mcp --help
just check
```
