# Goal References: History Capture Policy and DB-Backed Surfaces

## Repository

- `AGENTS.md` - dispatch project rules, lexicon, commands, and no-drift
  contract summary.
- `README.md` - user-facing quickstart and current behavior summary.
- `docs/usage/README.md` - operator documentation for CLI, sync, history,
  search, doctor, and MCP usage.
- `skills/dispatch/SKILL.md` - first-party Dispatch operating skill.
- `skills/dm/SKILL.md` - dispatch-backed direct-message skill.

## Architecture and Decisions

- `docs/adrs/0023-provider-event-log-and-history-index.md` - provider event log
  and normalized history substrate.
- `docs/adrs/0017-progressive-thread-sync-index.md` - sync semantics and compact
  SQLite cache.
- `docs/adrs/0018-top-level-thread-actions-and-search.md` - managed/unmanaged
  thread actions and search.
- `docs/adrs/0020-live-use-trust-contracts.md` - live-use trust and safety
  contracts.
- `docs/development/design.md` - current architecture and registry model.
- `docs/research/app-server-verification.md` - verified Codex App Server
  methods/events.

## Current Stack

- Packet branch: `docs/history-capture-policy-goal`
- Base branch: `feat/archive-aware-sync`
- Lower PR: `https://github.com/outfitter-dev/dispatch/pull/48`
- Current top PR: `https://github.com/outfitter-dev/dispatch/pull/49`
- New implementation branches should stack above the packet branch unless the
  stack has merged before execution begins.

## Source Areas

- `src/outfitter/dispatch/registry/store.py` - SQLite schema, migrations, and
  store APIs.
- `src/outfitter/dispatch/registry/models.py` - registry model contracts.
- `src/outfitter/dispatch/core/event_index.py` - Codex live event indexing and
  runtime reducers.
- `src/outfitter/dispatch/core/history_index.py` - `thread/read` history
  backfill.
- `src/outfitter/dispatch/core/handlers.py` - operator surface handlers.
- `src/outfitter/dispatch/core/sync.py` - progressive sync and JSONL indexing.
- `src/outfitter/dispatch/contracts/` - op registry and derived surfaces.
- `src/outfitter/dispatch/surfaces/` - CLI/MCP/control-socket projections.
- `src/outfitter/dispatch/core/config.py` and related config modules - runtime
  config surface if capture settings belong there.
- `src/outfitter/dispatch/daemon/` - daemon runtime and doctor/status paths.

## Tests and Fixtures

- `tests/registry/test_store.py`
- `tests/core/test_triggers.py`
- `tests/core/test_handlers.py`
- `tests/core/test_sync.py`
- `tests/fixtures/test_corpus.py`
- `tests/fixtures/`
- `tests/surfaces/test_parity.py`
- `tests/surfaces/test_derive_cli.py`
- `tests/surfaces/test_derive_mcp.py`
- `tests/test_doctor.py`

## Commands

- `uv run pytest <focused tests> -q`
- `uv run ruff check <touched files>`
- `uv run mypy <touched source files>`
- `uv run pytest tests/fixtures/test_corpus.py tests/registry/test_store.py -q`
- `uv run pytest tests/surfaces/test_parity.py tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py -q`
- `uv run dispatch schema <changed-command>`
- `uv run dispatch <changed-command> --help`
- `just check`
- `gt log --no-interactive`
- `gt create <branch> --message "<message>"` or repo-documented Graphite
  equivalent
- `gt submit --stack --restack --no-edit --no-interactive`

## Review Artifacts

- `.agents/goals/2026-07-01-history-capture-policy/tmp/reviews/` - local-review
  JSON reports for milestone and full-stack reviews.
- `.agents/goals/2026-07-01-history-capture-policy/RETRO.md` - durable execution
  ledger and final proof.
