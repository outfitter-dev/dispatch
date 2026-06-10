# Live-use Hardening - references

## Field report

- `/tmp/trails-dispatch-real-use-feedback-2026-06-07.md`
  - Registry schema v1 missing `lane_snapshots` and `lane_sync_sources`.
  - `dispatch up --json` unsupported; `dispatch up` no-oped against running
    daemon.
  - `dispatch new --model gpt-5.5-codex --text "$goal_prompt"` used a stale,
    guessed explicit model id and returned `sent: true`, `status: idle`, but no
    assistant response and no goal state.
  - `watch` surfaced unsupported model error; `get` did not.
  - Destroy cleanup required `printf 'y\n' | ... --json`.

## Architecture docs

- `docs/adrs/0000-contract-first-surface-derived.md`
  - Every surface is a pure projection of one op registry.
  - Parity tests must check behavior, not only names.
- `docs/adrs/0010-surface-projections-are-ergonomic-not-isomorphic.md`
  - Surfaces may group/rename/compose, but may not restate schemas, examples,
    safety intent, error behavior, or capability policy.
- `.claude/rules/contracts.md`
  - Overrides must be visible escape hatches, not default hand wiring.
- `.claude/rules/surfaces.md`
  - Surface modules contain projection wiring only.

## Code hot spots

- `src/outfitter/dispatch/contracts/derive_cli.py`
  - CLI projection, custom route functions, schema route table, destroy prompt.
- `src/outfitter/dispatch/surfaces/cli.py`
  - `doctor`, `up`, `down`, and `mcp` hand-written control commands.
- `src/outfitter/dispatch/core/handlers.py`
  - `new_lane`, `show`, send/goal handlers.
- `src/outfitter/dispatch/core/reactor.py`
  - `TurnFailed` currently updates status but does not persist message.
- `src/outfitter/dispatch/registry/store.py`
  - Schema migrations and registry state.
- `src/outfitter/dispatch/doctor.py`
  - Registry diagnostics and recovery hints.

## Existing tests

- `tests/surfaces/test_parity.py`
- `tests/surfaces/test_derive_cli.py`
- `tests/core/test_handlers.py`
- `tests/test_doctor.py`
- `tests/integration/test_daemon_e2e.py`
- `tests/integration/test_app_server.py`

## Verification commands

```bash
uv run pytest tests/surfaces/test_parity.py tests/surfaces/test_derive_cli.py tests/test_doctor.py tests/core/test_handlers.py -q
just check
```

Optional live smoke must use isolated runtime paths.
