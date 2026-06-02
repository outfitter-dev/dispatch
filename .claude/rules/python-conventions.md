# Python conventions

Cross-cutting rules for all of `src/outfitter/dispatch/`.

- **Tooling:** uv for everything (never bare `python`/`pip`). Ruff for lint + format. `mypy --strict` is the type gate — no `Any`, no untyped defs, no implicit `Optional`; narrow instead of `cast`. Pydantic v2 for all external/boundary data.
- **Layout:** `src/` layout, PEP 420 namespace. The package is `src/outfitter/dispatch/` with **no `__init__.py` at `src/outfitter/`** (namespace level) — only inside `dispatch/` and below. Distribution name `outfitter-dispatch`, import `outfitter.dispatch`.
- **Errors:** raise typed `DispatchError` subclasses; never raise bare `Exception`. Surfaces (not handlers) catch and project errors. No `Result` type — exceptions are the idiom; the discipline is the *taxonomy*, not the return shape.
- **Async:** the daemon and core are asyncio end-to-end. Never block the loop — use `aiosqlite` (not `sqlite3`), `asyncio.create_subprocess_exec` (not `subprocess`), and `run_in_executor` for unavoidable sync calls. Entry is `asyncio.run(...)`. The CLI is a separate **sync** process that talks to the daemon over its Unix socket; keep that boundary clean.
- **Validation at the edge:** parse external data (App Server messages, CLI input, env, config) into Pydantic models at the boundary; trust types internally. Config via `pydantic-settings`.
- **Files:** small and focused (<200 LOC healthy; >400 means split). One clear purpose per module; narrow public interfaces.
- **Logging:** `structlog`; structured events. The audit log (`actions_log`) is the record of every send/action.
- **Tests:** pytest + pytest-asyncio. Integration tests spawn a **real ephemeral** `codex app-server` with an isolated `CODEX_HOME` (temp dir) and `ephemeral:true` lanes — never the user's `~/.codex` or live daemon. Use an injectable clock so time-trigger tests are deterministic.
