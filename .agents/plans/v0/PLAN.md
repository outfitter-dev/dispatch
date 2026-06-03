# dispatch v0 — implementation plan

Execution plan for [`design.md`](../../../docs/development/design.md). Built as a **Graphite stack: one branch per phase**, each independently reviewable, green, and **locally reviewed before the next phase starts**. Goal loop + completion contract: [`GOAL.md`](./GOAL.md). Running ledger: [`RETRO.md`](./RETRO.md). References: [`REFS.md`](./REFS.md). Decisions: [`docs/adrs/`](../../../docs/adrs/) (0000–0009). Conventions: [`.agents/plans/PLANNING.md`](../PLANNING.md).

## Execution model

- **One phase = one Graphite branch**, stacked in order on `main`. `gt create <branch> -am`, `gt submit --stack --draft` (after the repo is synced to Graphite). PRs stay **draft** until their local review gate passes.
- **Verification ladder (every phase, must be green before review):**
  1. `just check` = `ruff check` + `ruff format --check` + `mypy --strict` + `pytest`
  2. examples-as-tests (`test_examples(registry)`) once the contract layer exists
  3. integration tests against a **real ephemeral `codex app-server`** with an isolated `CODEX_HOME` + `ephemeral:true` lanes (never the user's `~/.codex` or live daemon)
- **Local review gate (between phases):** when a phase branch is green + self-reviewed, request a **local review** in the [code-review output contract](#review-contract) — `Overall score: n/5`, `P0–P3` findings with `file:line` + Prompt-To-Fix. Fix P0–P2 (P3 fix-if-cheap or record deferred), record the round in `RETRO.md`, and **do not start the next phase until the gate passes** (local reviewer ≥ 4/5 with no open P0/P1/P2, or explicit user OK).
- **Resumability:** state lives in the packet, not chat. On resume, read `RETRO.md` (where execution left off) + `gt log` (stack state) + open PRs. `RETRO.md` is updated before every handoff/ready/merge.
- **TDD:** write the failing test (or the op example) first, then the code.

<a id="review-contract"></a>**Review contract** (request this shape from the local reviewer):
```
Overall score: n/5
Summary: <one-line judgment>
Findings:
- P0|P1|P2|P3 — <file:line> — <finding>
  Prompt To Fix With AI: <concise fix prompt>
No-findings statement: <what was inspected, residual risk>
```
Severity: P0 cannot-proceed · P1 correctness/contract regression · P2 quality/docs/coverage to fix before ready (docs correctness is P2) · P3 style/taste.

## Phases

### Phase 0 — Scaffold & tooling · branch `chore/scaffold`
Installable, lint/type/test-green skeleton; `dispatch --help` and `dispatchd --help` run.
- `uv init`; `pyproject.toml` (`outfitter-dispatch`, `requires-python>=3.13`, hatchling for the `outfitter.dispatch` namespace under `src/`, **no `__init__.py` at `src/outfitter/`**).
- Deps: `typer rich pydantic pydantic-settings aiosqlite structlog croniter mcp`; dev: `ruff mypy pytest pytest-asyncio`.
- `[project.scripts]`: `dispatch = "outfitter.dispatch.cli:app"`, `dispatchd = "outfitter.dispatch.daemon.__main__:main"`.
- Config: ruff (strict + formatter), mypy strict, pytest asyncio mode, `.python-version`; `lefthook.yml` (pre-commit ruff+mypy, pre-push pytest); `justfile` (`check test lint typecheck fmt run`); GitHub Actions via `astral-sh/setup-uv`.
- Stub `cli.py` + `daemon/__main__.py` so entrypoints resolve.
- **Verify:** `just check` green; `uv run dispatch --help`; CI green. **Gate → review.**

### Phase 1 — App Server client + gating spikes · branch `feat/client`
Typed async client over one `codex app-server` (stdio JSONL), multiplexing lanes. Reusable; importable without the daemon.
- `client/`: `transport` (spawn app-server, async stdin/stdout, EOF/crash detect), `router` (demux by id/`threadId` → per-lane streams + global), `models` (Pydantic wire types from the pinned binary's generated schema), `client` (initialize · thread start/resume/list/read/archive · turn start/steer/interrupt · inject_items · approval responder · `events()`).
- **Normalized `LaneEvent` projection** at the client boundary (ADR-0007).
- **Slice-0 spikes (record in `RETRO.md` + `.agents/notes/`):**
  - *Cross-process safety* → gates ADR-0005 (owned=rw; attached=observe-only until proven; advisory lock is dispatch-local and cannot gate the desktop app).
  - *Concurrent-lane / backpressure (F)* → N lanes, concurrent active turns, one stdio stream; observe head-of-line blocking.
- **Verify:** integration suite (promoted from `spikes/`) passes — initialize; read-only turn → `pong`; inject_items recall; approval accept resumes turn; persisted-resume fan-out; `thread/list` reads `result.data`. Encode gotchas: `thread/start.sandbox` string vs `turn/start.sandboxPolicy` object; `turn/steer` needs `expectedTurnId`; file-change approval has no diff (correlate by `itemId`). **Gate → review + spike findings sign-off.**

### Phase 2 — Contract layer + registry + CLI · branch `feat/contracts-cli`
Ops authored once; CLI derived; end-to-end through the daemon.
- `contracts/`: `Op` (input/output Pydantic, `intent`, `idempotent`, examples, async handler), `registry`, `errors` (`DispatchError` taxonomy + per-surface projection), `examples` (`test_examples`), `derive_cli`.
- **Handler `Ctx`/DI** (ADR-0006): `{client, registry, log, abort}`; handlers `(input, ctx)`; tests inject fakes.
- `registry/store.py` (aiosqlite): `lanes`, `triggers`, `actions_log`.
- `core/handlers.py`: `open attach send steer brief interrupt show roster archive`; `→ @project:name` titles for `open`; default write guard = advisory lock + idle-check; **attached lanes observe-only** (ADR-0005).
- `daemon/control.py`: Unix-socket control API — **JSON-RPC-lite over JSONL with notifications** (ADR-0008). `surfaces/cli.py`: derive Typer app; route to daemon; Rich rendering.
- **Verify:** examples-as-tests green; integration — open/send/show/roster/archive via daemon; error→exit-code projection. **Gate → review.**

### Phase 3 — Scheduler + reactor + triggers · branch `feat/triggers`
Automated pings on time + event triggers; our own scheduler (ADR-0003).
- `core/scheduler.py` (asyncio time wheel; interval + cron via `croniter`; **injectable clock**); `core/reactor.py` (consume `LaneEvent`s); `core/triggers.py` (`when`/`action`/`guard`; guards `idle_only min_interval dedupe`; conditional-guard seam, interface only).
- Persist triggers; `surfaces/cli` `triggers add|rm|pause|resume|list`. `waiting_on_approval` as an event trigger; unhandled default = `decline`.
- **Verify:** time trigger fires a send on a short interval (fake clock); each event trigger fires once on its event; guards enforced; every firing in `actions_log`. **Gate → review.**

### Phase 4 — MCP surface · branch `feat/mcp`
dispatch is an MCP server, derived from the same ops — zero drift.
- `contracts/derive_mcp.py` (tool name from op id; `inputSchema` from `model_json_schema()`; annotations from `intent`/`idempotent`; error → `isError`+`_meta`). `surfaces/mcp.py` + `dispatch mcp` stdio entrypoint routing to the daemon (ADR-0009 lifecycle).
- **Verify:** `dispatch mcp` tools == registry ops; a tool call (open/send/roster) end-to-end; **parity test** asserts per-op CLI options ↔ MCP inputSchema/annotations ↔ error projection (not just op names). **Gate → review.**

### Phase 5 — Daemon lifecycle polish · branch `feat/daemon-lifecycle`
Robust always-on daemon.
- Supervision (app-server EOF → restart → re-resume attached lanes → resubscribe); `up`/`down`/`status`/`log`; structlog + `actions_log` surfacing; macOS `launchd` LaunchAgent + `dispatch up` bootstrap (ADR-0009 singleton).
- **Verify:** kill app-server mid-run → daemon recovers + lanes resume; `status` shows health+roster; launchd keeps `dispatchd` alive. **Gate → review.**

## Definition of done (v0)
Open/attach lanes (owned rw; attached observe-only unless ADR-0005 cleared), send/steer/brief/interrupt, time + event triggers, all via a CLI **and** an equivalent MCP server derived from one contract set, hosted by a recoverable daemon — with examples-as-tests + integration against a real ephemeral app-server, `ruff` + `mypy --strict` + `pytest` green in CI, each phase locally reviewed, and `RETRO.md` finalized.

## Risk register
- **Cross-process contention** (own vs desktop app-server, shared `~/.codex`): bounded by the Phase-1 spike; gates ADR-0005. Advisory lock is dispatch-local.
- **Single-stream backpressure:** one stdio connection multiplexes all lanes; head-of-line under concurrent turns untested → Phase-1 spike (F).
- **App-server version drift:** pin the binary; drive it directly (not `openai-codex`, which pins older CLI 0.132 vs local 0.136); regenerate schema per binary.
- **MCP transport:** stdio first; SSE/streamable-HTTP later.
- **Scheduler correctness:** injectable clock for deterministic time-trigger tests.
- **Graphite sync:** repo must be synced to the Graphite account before `gt submit` works (current blocker; PRs opened via `gh` until then).
