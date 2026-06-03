# dispatch — v0 implementation plan

Execution plan for the design in [`design.md`](./design.md). Phased into stacked Graphite PRs. Each phase is independently reviewable, ends green (ruff + mypy --strict + pytest), and builds on the prior. TDD throughout: write the failing test (or the trail example) first, then the code.

References: [`REFS.md`](./REFS.md).

## Conventions

- **Language/tooling:** Python 3.13, uv, hatchling, `src/` layout, PEP 420 namespace (`src/outfitter/dispatch/`, no `__init__.py` at the `outfitter/` level). Ruff (lint+format), mypy `--strict`, Pydantic v2.
- **Quality gate per PR:** `just check` = `ruff check` + `ruff format --check` + `mypy --strict` + `pytest`. Green before submit.
- **Tests with real dependencies:** integration tests spawn a real ephemeral `codex app-server` with an isolated `CODEX_HOME` (temp dir) and `ephemeral:true` lanes — never the user's `~/.codex` or live daemon.
- **No drift:** every surface (CLI, MCP, remote) is derived from the one op registry. A parity test asserts CLI commands and MCP tools enumerate the same ops.
- **Errors:** typed `DispatchError` hierarchy; raise in handlers, project at the surface boundary (exit code / MCP `_meta` / remote code). No `Result` type.

## Phase 0 — Scaffold & tooling  (PR: `chore/scaffold`)

Goal: an installable, lint/type/test-green skeleton; `dispatch --help` and `dispatchd --help` run.

- `uv init` → `pyproject.toml`: `name = "outfitter-dispatch"`, `requires-python = ">=3.13"`, build-backend hatchling configured for the `outfitter.dispatch` namespace package under `src/`.
- Runtime deps: `typer`, `rich`, `pydantic`, `pydantic-settings`, `aiosqlite`, `structlog`, `croniter`, `mcp`. (No `dateutil`/RRULE in v1 — cron + interval only.)
- Dev deps: `ruff`, `mypy`, `pytest`, `pytest-asyncio`.
- `[project.scripts]`: `dispatch = "outfitter.dispatch.cli:app"`, `dispatchd = "outfitter.dispatch.daemon.__main__:main"`.
- Config: `ruff.toml` (strict ruleset + formatter), `mypy` strict in `pyproject`, `pytest` asyncio mode, `.python-version`.
- `lefthook.yml` (pre-commit: ruff + mypy; pre-push: pytest), `justfile` (`check`, `test`, `lint`, `typecheck`, `run`, `fmt`), GitHub Actions CI using `astral-sh/setup-uv`.
- Stub `cli.py` (empty Typer app) and `daemon/__main__.py` so entrypoints resolve.

Acceptance: `uv run dispatch --help` works; `just check` green on the skeleton; CI green.

## Phase 1 — App Server client  (PR: `feat/client`)

Goal: a typed, async client that drives one `codex app-server` over stdio JSONL and multiplexes many lanes. This is the reusable foundation; importable without the daemon.

- `client/transport.py`: spawn `codex app-server --listen stdio://`; async stdin writer + stdout line reader; lifecycle (start/close/crash detection via EOF).
- `client/router.py`: demux responses by request `id` and notifications by `threadId` into per-lane async queues + a global stream (mirror the verified message-router pattern).
- `client/models.py`: Pydantic models for the wire messages we use (initialize, thread/turn/item params + notifications, approval req/resp). Derived from the generated JSON schema of the pinned binary.
- `client/client.py`: primitives — `initialize`, `thread_start/resume/list/read/archive`, `turn_start/steer/interrupt`, `inject_items`, and an approval responder hook. Async `events(thread_id|all)`.
- **Slice-0 spike (record findings in `.agents/notes/`):** verify cross-process safety — our app-server + a second app-server touching one shared persisted thread. Decide whether the default guard (idle-only + advisory lock) is sufficient.

Tests (integration, isolated `CODEX_HOME`): promote `/tmp/codex_{stdio,dm,lab4,fanout}.py`. Assert: initialize handshake; start thread + run a read-only turn → `pong`; `inject_items` then recall; approval accept loop resumes the turn; persisted-thread resume yields live fan-out; `thread/list` reads `result.data`.

Gotchas baked into tests: `thread/start.sandbox` = string enum vs `turn/start.sandboxPolicy` = object; `turn/steer` needs `expectedTurnId`; file-change approval has no diff (correlate by `itemId`).

Acceptance: integration suite passes; cross-process finding recorded; client importable standalone.

## Phase 2 — Contract layer + registry + CLI  (PR: `feat/contracts-cli`)

Goal: ops authored once; CLI derived from them; end-to-end through the daemon against a real ephemeral app-server.

- `contracts/op.py`: the `Op` definition (`input`/`output` Pydantic models, `intent`, `idempotent`, `examples`, async `handler`). `contracts/registry.py`: collect ops. `contracts/errors.py`: `DispatchError` hierarchy + per-surface projection helpers. `contracts/examples.py`: `test_examples(registry)`.
- `contracts/derive_cli.py`: project registry → Typer commands (fields → options; `intent: destroy` → confirm prompt; `read` → none).
- `registry/store.py` (aiosqlite): tables `lanes`, `triggers`, `actions_log`; schema init/migration; typed accessors.
- `core/handlers.py`: implement ops `open` (`thread/start` + register), `attach` (`thread/resume` + register), `send` (`turn/start`), `steer`, `brief` (`inject_items`), `interrupt`, `show` (`thread/read`+tail), `roster` (`thread/list`+registry+status), `archive` (`thread/archive`). Adopt `→ @project:name` titles for `open`.
- `daemon/control.py`: Unix-socket server exposing the derived method table (JSON over the socket). `daemon/app.py`: own the client + registry + control socket.
- `surfaces/cli.py` + `cli.py`: Typer app that derives commands and routes each to the daemon control socket; Rich rendering for `roster`/`status`/`log`.
- Default write guard: per-lane advisory lock + idle-check in the registry.

Tests: examples-as-tests for each op; integration — `open` a lane, `send`, `show`, `roster`, `archive` via daemon against ephemeral app-server; error projection (exit codes).

Acceptance: full CLI roundtrip works; `test_examples` green.

## Phase 3 — Scheduler + reactor + triggers  (PR: `feat/triggers`)

Goal: automated pings on time + event triggers; our own scheduler.

- `core/scheduler.py`: asyncio time wheel; interval + cron next-fire via `croniter` (no RRULE in v1).
- `core/reactor.py`: consume the client event stream; map events (`idle_for`, `turn_completed`, `waiting_on_approval`) → trigger evaluation.
- `core/triggers.py`: trigger model (`when` / `action` / `guard`), guards (`idle_only`, `min_interval`, `dedupe`); the conditional-guard seam (not implemented, interface only).
- Persistence in `registry` (`triggers` table); `surfaces/cli` `triggers add|rm|pause|resume|list`.
- `waiting_on_approval` surfaced as an event trigger; safe default decision `decline` when unhandled.

Tests: a time trigger fires a `send` on a short interval (fake clock); each event trigger fires once on the matching event; guards enforced; every firing written to `actions_log`.

Acceptance: time + event triggers demonstrably fire and are audited; guards work.

## Phase 4 — MCP surface  (PR: `feat/mcp`)

Goal: dispatch is an MCP server, derived from the same ops — zero drift.

- `contracts/derive_mcp.py`: registry → MCP tool defs (tool name from op id; `inputSchema` from `model_json_schema()`; annotations from `intent`/`idempotent`; error → `isError` + `_meta`).
- `surfaces/mcp.py` + `dispatch mcp` entrypoint: stdio MCP server (via `mcp` SDK) whose tool handlers route to the daemon control socket.
- Parity test: the set of MCP tools equals the set of CLI commands equals the registry ops.

Acceptance: `dispatch mcp` lists tools = ops; a tool call (open/send/roster) works end-to-end; parity test green.

## Phase 5 — Daemon lifecycle polish  (PR: `feat/daemon-lifecycle`)

Goal: a robust always-on daemon.

- Supervision: detect app-server EOF → restart → re-`resume` attached lanes → resubscribe.
- `up` / `down` / `status` / `log` commands; structured logs (structlog) + `actions_log` surfacing in `log`.
- macOS `launchd` LaunchAgent plist + `dispatch up` bootstrap.

Acceptance: kill the app-server mid-run → daemon recovers and lanes resume; `status` shows health + roster; launchd keeps `dispatchd` alive.

## Out of scope for v0 (tracked for later)

Remote-control surface (`derive_remote` + authenticated network transport); conditional-trigger guards (seam only); full approval policy engine; dashboard/TUI; Claude/crew backend; multi-user.

## Definition of done (v0)

Open/attach lanes (own + existing), send/steer/brief/interrupt, set time + event triggers, all via a CLI and an equivalent MCP server derived from one contract set, hosted by a recoverable daemon — with examples-as-tests and integration tests against a real ephemeral app-server, ruff + mypy --strict + pytest green in CI.

## Risk register

- **Cross-process contention** (own vs desktop app-server on one shared thread): resolved/bounded in Phase 1 slice-0 spike; may tighten the default guard.
- **App-server version drift:** pin the binary; drive it directly (not via `openai-codex`, which pins older CLI 0.132 vs local 0.136). Regenerate schema per binary.
- **MCP transport:** stdio first; SSE/streamable-HTTP later.
- **Scheduler correctness:** use a fake/injectable clock so time triggers are deterministically testable.
