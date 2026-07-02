# AGENTS.md

Primary fieldguide for agents working on **dispatch** — a local control plane for orchestrating Codex agent lanes (threads) over the Codex App Server. One authored contract per operation is projected onto multiple surfaces (CLI now, MCP now, remote later) with no drift.

`AGENTS.md` is the canonical project guidance file. `CLAUDE.md` is a thin shim that points here. Path-scoped rules live in `.claude/rules/` and are delivered into their directories as symlinked `AGENTS.md` files (see [agent-docs convention](.claude/rules/agent-docs.md)).

## Commands

Use repo tasks first (justfile), backed by uv:

```bash
just check       # ruff check + format --check + mypy --strict + pytest (the gate)
just test        # pytest
just lint        # ruff check
just fmt         # ruff format
just typecheck   # mypy --strict
just scenario -- tests/scenarios/basic_coordination.toml
just run -- ...   # run the dispatch CLI in-tree
uv run dispatch --help
uv run dispatchd --help
```

Always go through `uv` (never a bare `python`/`pip`). `uv sync` to install, `uv add <pkg>` to add deps.

## Project Overview

dispatch owns one `codex app-server` subprocess (stdio JSONL, shared `~/.codex`), multiplexes many lanes over it, and exposes them through derived surfaces. The architecture makes consistency easier than drift: every surface (CLI/MCP/remote) is *derived* from one op registry, so they cannot diverge. We orchestrate the Codex App Server (itself a one-protocol-many-surfaces design) and apply the same discipline to our own surfaces.

## Project docs

- `docs/development/design.md` — approved design spec.
- `docs/development/semantic-history-search.md` — local history search and future embedding policy.
- `docs/adrs/` — architecture decision records (start from `template.md`; index in `README.md`).
- `docs/research/` — App Server verification + orchestration thesis (the findings dispatch is built on).
- `docs/usage/` — operator docs for the CLI, MCP, triggers, and plugin setup.
- `.agents/plans/v0/` — phased plan (`PLAN.md`) + references (`REFS.md`); tracked.
- `spikes/` — App Server probe scripts; seed of the integration suite.
- `tests/fixtures/` — small named App Server, JSONL, CLI-smoke, and registry fixtures.
- `tests/scenarios/` — live agent workflow fixtures run intentionally with `just scenario`.
- `.agents/notes/` — working notes, session recaps, learnings; **gitignored, local only**.
- `skills/` — first-party Codex skills for operating dispatch (`dispatch`) and dispatch-backed direct messages (`dm`).
- `plugins/dispatch/` — workspace-local Codex plugin bundle exposing the skills and MCP server.

Read `docs/development/design.md` and `.agents/plans/v0/PLAN.md` before implementing. Record working findings in `.agents/notes/`; promote durable decisions into `docs/adrs/`.

## Lexicon

Use the project language consistently:

- **lane** — internal term for a managed Codex thread (own or attached) with registry state.
- **thread** — user-facing Codex conversation/session. Public CLI/help/docs should prefer "thread" unless the managed-lane authority distinction matters.
- **ref** — dispatch-local short stable selector for a managed lane. Full Codex thread ids are always accepted; titles and `@handles` are mutable labels.
- **op** — one authored operation (input/output/intent/examples/handler). The contract unit. Not "command" or "tool" (those are surface projections of an op).
- **surface** — a derived rendering of the op registry: CLI, MCP, remote. Surfaces are projected, never hand-written per-op.
- **trigger** — an automated when→action→lane binding (time or event). Not "rule" (collides with agent rules), "automation", or "job".
- **daemon** (`dispatchd`) — the long-lived host owning the app-server and core; the CLI is a thin client to it.
- **register/registry** — the durable store of lanes, refs, sync state, and triggers.

## Core rules (summary; full detail in `.claude/rules/`)

- **Author once, derive surfaces.** Add behavior as an op in `contracts/`; route
  it through the derived CLI/MCP projection instead of hand-writing a separate
  command or tool. CLI flags, MCP grouped-tool schemas/annotations, and
  exit/error codes are derived. See [contract-layer](.claude/rules/contracts.md).
- **Typed exceptions, projected at the boundary.** Handlers raise `DispatchError` subclasses; each surface projects them (exit code / MCP `_meta` / remote code). No `Result` type — idiomatic Python.
- **Examples are tests.** Every op carries examples; `test_examples(registry)` runs them in CI.
- **intent drives behavior.** `read` = no CLI confirm / MCP `readOnlyHint`; `destroy` = confirm / `destructiveHint`.
- **App Server access only via `client/`.** Never spawn or speak to `codex app-server` outside the client layer. See [client rules](.claude/rules/client.md).
- **Async core, sync CLI.** The daemon is asyncio end-to-end; the CLI is a thin sync client over the control socket. No blocking calls in the loop (use `aiosqlite`, asyncio subprocess, `run_in_executor`). See [python-conventions](.claude/rules/python-conventions.md).
- **Never touch the user's live state in tests.** Integration tests use a real ephemeral app-server with an isolated `CODEX_HOME` and `ephemeral:true` lanes.
- **Fixtures should be exercised.** Add checked-in cases under `tests/fixtures/` only when a test loads them; prefer Python builders over binary SQLite fixtures.
- **Live scenarios are opt-in.** Scenario fixtures start real isolated Dispatch/Codex daemons and make model calls; keep them small, synthetic, low-effort, and outside `just check`.

## Source control

Trunk-based on `main` with Graphite (`gt`). Stacked PRs, one phase per stack slice (see `PLAN.md`). Keep commits small and reviewable.
