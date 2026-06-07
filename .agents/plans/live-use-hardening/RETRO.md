# Live-use Hardening - execution ledger

Durable execution ledger for the live-use trust hardening goal.

## Timeline

- 2026-06-07: Created packet on `feat/live-use-hardening` after the Trails
  real-use report showed green tests missing operator trust failures.
- 2026-06-07: Implemented runtime turn-state persistence, native `new --goal`,
  honest `message_accepted` launch output, scriptable daemon lifecycle output,
  destroy-command confirmation flags, registry migration recovery, and explicit
  CLI projection/control manifests.
- 2026-06-07: Updated README, usage docs, dispatch skill, plugin README,
  development design notes, agent rules, and ADR-0020.

## Checks

- `uv run pytest tests/test_doctor.py tests/surfaces/test_parity.py tests/surfaces/test_derive_cli.py tests/core/test_handlers.py tests/registry/test_store.py -q`
  - `103 passed`
- `uv run pytest -q`
  - `210 passed, 9 deselected`
- `just check`
  - `ruff check`: passed
  - `ruff format --check`: passed
  - `mypy src tests`: passed
  - `pytest`: `210 passed, 9 deselected`
  - `uv build`: built `outfitter_dispatch-0.4.0` sdist/wheel
  - `scripts/check_package_contents.py`: passed
- CLI smoke:
  - `uv run dispatch schema new | jq -r ...`
    - verified goal and `message_accepted` schema descriptions.
  - `uv run dispatch schema 'list --unmanaged' | jq -r .op`
    - returned `discover`.
  - `uv run dispatch schema 'tail --follow'`
    - exited `2` with a clean unknown-command error, matching current docs.
  - `uv run dispatch registry migrate --help`
    - showed JSON/text, backup, and controlled-running options.

## Review

- P0/P1/P2 review pass:
  - Verified projection guardrails cover op-backed CLI routes, schema spellings,
    and full CLI surface-control allowlist.
  - Verified synchronous `turn/start` failures no longer leave registered lanes
    looking idle; `new`/`send` now persist latest error state before re-raising.
  - Verified `TurnFailed.message` projects through reactor -> registry -> `get`.
  - Verified `/goal ...` initial text is rejected unless callers use native
    `--goal`, and native goal set happens before the initial turn.
  - Verified old registry recovery has doctor guidance plus `registry migrate`
    tests, including daemon-running refusal.
  - Verified docs/skill/plugin/rules/ADR describe the changed behavior and
    current limitations.
- Unresolved P0/P1/P2: none found in local review.

## Deferred

- Account/model preflight remains deferred. The current App Server client
  accepts model strings on thread/turn options but does not expose a cheap,
  reliable account-specific model support check in dispatch's verified contract.
  The implemented mitigation is to persist and expose App Server failures through
  ordinary status surfaces instead of requiring raw `watch`.
- Infinite streaming remains deferred. `watch` is still a bounded live event
  sample over a request/response control socket; a subscription-capable control
  socket remains future work.
