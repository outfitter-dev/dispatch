# dispatch — design spec (2026-06-02)

A local control plane for orchestrating Codex agent lanes (threads) over the Codex App Server: create/attach lanes, send work or context, queue delivery, stop active turns, and automate pings on time- and event-based triggers. One authored contract per operation is projected onto multiple surfaces — CLI now, MCP now, remote control later — with no drift.

Status: approved design, implemented through v0. Companion research (verified against `codex-cli 0.136.0-alpha.2`): [`docs/research/app-server-verification.md`](../research/app-server-verification.md) and [`docs/research/orchestration-thesis.md`](../research/orchestration-thesis.md). Decisions: [`docs/adrs/`](../adrs/). Execution ledger: [`../../.agents/plans/v0/RETRO.md`](../../.agents/plans/v0/RETRO.md).

## Naming

- Distribution (PyPI): `outfitter-dispatch` · Import package: `outfitter.dispatch` (PEP 420 namespace) · CLI binary: `dispatch` · daemon binary: `dispatchd`.
- Rationale: PyPI has no npm-style scopes; `outfitter-dispatch` mirrors `@outfitter/*`, dodges the taken `dispatch` PyPI name and the Netflix "Dispatch" brand, and still gives the `dispatch` command. Lanes/coordinators reuse the existing `→ @project:name` / `@Name` title conventions so they read natively in the Codex desktop app.

## Goals / non-goals

Goals (v1): a single daemon that owns one Codex app-server and drives many lanes; a typed CLI and an MCP server, both derived from one contract set; time + event triggers; durable registry of lanes and triggers; full read/write on self-spawned owned lanes. Existing desktop lanes can be attached, but v0 keeps them observe-only per ADR-0005.

Non-goals (v1): Claude/crew backend; conditional triggers (seam only); dashboard/TUI; full approval policy engine; multi-user; remote-control surface (planned v2).

## Guiding principle (Trails-inspired, idiomatic Python)

Adopt the Trails philosophy — **author what's new, derive what's known, override what's wrong** — so we never maintain two parallel surface areas. Author each operation once (input/output models, intent, examples, handler); derive every surface (CLI flags, MCP tool defs + annotations, remote methods, exit/error codes) from it. Be inspired by Trails where it helps and is idiomatic Python; diverge where Python's idioms are better — notably **typed exceptions normalized at the surface boundary instead of a `Result` type**.

The recursive nicety: the Codex App Server is itself a one-protocol-many-surfaces design; dispatch orchestrates it and applies the same discipline to its own surfaces.

## Architecture

Single long-lived **daemon** (`dispatchd`):
- Spawns and owns **one** `codex app-server --listen stdio://` subprocess, sharing `CODEX_HOME=~/.codex` so it sees existing desktop threads. Communicates in newline-delimited JSON (the only bare-JSONL transport; unix/ws are WebSocket-framed and the managed daemon's control socket is auth-gated — see research notes).
- A **message router** demuxes responses (by request id) and notifications (by `threadId`) from the single app-server connection into per-lane async event streams. (Verified pattern; mirrors the Python SDK's internal router.)
- Hosts the **core**: registry, scheduler, reactor, and the contract handlers. Executes all handlers.
- Exposes a **control API** over a Unix domain socket — the canonical projection of the contract set.

Surfaces are thin renderings of the same contracts:
- **CLI** (`dispatch`): a separate, synchronous process that parses argv (Typer commands derived from contracts) and calls the daemon's control API.
- **MCP** (`dispatch mcp`): a stdio MCP server entrypoint (spawned by the MCP client, e.g. Claude/Codex) exposing tools derived from the same contracts; tool calls route to the daemon control API, same as the CLI.
- **Remote control** (v2): the control API exposed over an authenticated network transport — a third projection, cheap because the contract already exists.

```
                 ┌── CLI (Typer)  ─┐
contracts ──────►├── MCP (mcp SDK) ─┤──► daemon control API ──► core handlers ──► app-server client ──► codex app-server
 (authored once) └── remote (v2) ──┘                                                                      (one process, many lanes)
```

## Module layout (clean layers; `client` + `registry` importable without the daemon)

`src/outfitter/dispatch/` (PEP 420 namespace — no `__init__.py` at the `outfitter/` level):
- `client/` — typed App Server client. Spawns app-server, stdio JSONL, message router, async event streams. Primitives: initialize · thread start/resume/list/read/archive · turn start/steer/interrupt · inject_items · approval responder. Pydantic models for wire messages. Importable standalone.
- `contracts/` — the op definitions (one per operation) + the registry + projection functions (`derive_cli`, `derive_mcp`, `derive_remote`) + error taxonomy.
- `registry/` — SQLite (aiosqlite) store of lanes, triggers, and an actions audit log. Importable standalone.
- `core/` — scheduler (time triggers), reactor (event triggers), trigger model + guards, and the handlers that fulfill the contracts.
- `daemon/` — wires core + client + control socket; owns app-server lifecycle and supervision.
- `surfaces/` — `cli.py` (Typer projection), `mcp.py` (MCP projection); `remote.py` later.
- `cli/` — thin entrypoint; `dispatchd` entrypoint.

## The contract layer

An **op** is authored once:
- `input`: Pydantic model (fields → CLI flags, → MCP `inputSchema` via `model_json_schema()`).
- `output`: Pydantic model (→ MCP `structuredContent`/`outputSchema`, → CLI rendering).
- `intent`: `read | write | destroy` (→ CLI confirm behavior; → MCP `readOnlyHint`/`destructiveHint`).
- `idempotent`: bool (→ MCP `idempotentHint`).
- `examples`: list of input + expected output/error (→ docs + assertions via `test_examples()`).
- `handler`: `async (input, ctx) -> output` running in the daemon; raises typed `DispatchError`s.

Projections (pure functions over the registry, mirroring Trails' `derive* → create* → surface`):
- `derive_cli(registry) -> Typer app` — an ergonomic command tree over the op registry; command routes may group/compose ops, but flags and schemas derive from input models and `intent` drives confirm prompts.
- `derive_mcp(registry) -> [McpTool]` — grouped workflow/safety tools with an `op` selector; per-op schemas derive from input/output models and annotations derive from `intent`/`idempotent`.
- `derive_remote(registry)` — control-socket method table; later the network surface.

**Error taxonomy** (transport-independent, projected per surface): a `DispatchError` hierarchy (e.g. `NotFoundError`, `LaneBusyError`, `ApprovalRequiredError`, `AppServerError`). Each surface catches and projects: CLI → exit code + Rich-rendered message; MCP → `isError` + `_meta` code; remote → JSON-RPC error. Handlers raise; surfaces normalize. No `Result` type.

**examples = tests**: `test_examples(registry)` runs each op's examples as assertions in CI.

## Command surface (v1)

- Daemon lifecycle: `up` / `down` (process) · `daemon status` · `daemon log`
- Lane creation: `new <name> [--preset ...] [--text ...] [--no-send]`
- Lane reads/discovery: `lane get <lane>` · `lane status <lane>` · `lane list` ·
  `lane list --unmanaged` · `lane sync <lane>` · `lane tail <lane>` ·
  `lane tail <lane> --follow`
- Lane management/history: `lane attach <thread> [--sync]` · `lane rename <old> <new>` ·
  `lane fork <lane>` · `lane rollback <lane>` · `lane compact <lane>` ·
  `lane archive <lane>`
- Sending: `send <lane> "…"` with `--mode send|steer|queue|interject|context`
  and equivalent mutually exclusive `--steer`, `--queue`, `--interject`,
  `--context`; `stop <lane>` / `stop --lane <lane>` is cancel-only.
- Goals: `goal status <lane>` · `goal set <lane> <objective>` · `goal clear <lane>`
- Triggers: `trigger add` · `trigger list` · `trigger rm <id>` ·
  `trigger pause <id>` · `trigger resume <id>`
- Schemas: `schema <command>` prints derived input/output schemas for shell automation.

MCP tools are an ergonomic projection of the same ops, grouped by workflow and safety
boundary rather than forced to be one tool per op. The noun for a managed thread is
**lane**.

## App Server integration (verified primitives → ops)

| Op | App Server call | Notes (verified) |
| --- | --- | --- |
| `open` | `thread/start` (then register) | `sandbox` is a STRING enum (`read-only`/`workspace-write`/`danger-full-access`); persists by default (`ephemeral:false`) → spawned lanes show in desktop app, matching the `→ @project:name` convention. |
| `new` | `thread/start` + `thread/name/set` + optional `turn/start` | Applies `.dispatch/config.toml` defaults/presets, name prefixes, verified session/turn options, and optional initial payload. |
| `attach` | `thread/read(includeTurns:false)` (+ register) | Metadata-only by default: verifies the thread id, registers an observe-only attached lane, and stores sync state without loading turn history. `--sync` runs a quick local index refresh after registration. |
| `sync` (`lane sync`) | `thread/read(includeTurns:false)` + bounded local JSONL parsing | Refreshes dispatch's index/cache for a lane: source file identity, sync state, latest event timestamp, latest turn id, preview, and selected metadata. Does not copy transcripts wholesale or grant attached-lane write authority. |
| `send` (`mode=send`) | `turn/start` | Delivers a message the lane processes + answers. The DM/`send_message_to_thread` equivalent. `sandboxPolicy` here is an OBJECT (`{type:"readOnly"}`) — different encoding than `thread/start.sandbox`. |
| `send` (`mode=queue`) | registry queue + later `turn/start` | Persists local queued delivery and starts one queued turn when the lane becomes idle. |
| `send` (`mode=steer`) | `turn/steer` | Requires `expectedTurnId` (the active turn id from `turn/started`). Adds input to an in-flight turn. |
| `send` (`mode=context`) | `thread/inject_items` | Silent model-visible context injection (Responses-API items); no turn runs. Trigger actions still call this lower-level behavior `brief`. |
| `send` (`mode=interject`) | `turn/interrupt` + `turn/start` | Requires an active turn id, cancels that turn, then starts replacement work. |
| `stop` | `turn/interrupt` | Requires an active turn id and cancels the active turn without replacement text. |
| `archive` | `thread/archive` | Reversible via `thread/unarchive` for persisted lanes. If App Server reports `no rollout found` for an owned no-rollout lane, dispatch archives the local registry entry so throwaway lanes can be cleaned up. |
| `roster` (`lane list`) | `thread/list` + registry + status | List results are under `result.data` (NOT `result.threads`); `useStateDbOnly:true` reads the persisted store. |
| `discover` (`lane list --unmanaged`) | `thread/list` state DB only | Lists persisted Codex sessions that could be attached; it does not resume or register them. |
| `show` (`lane get/status`) | registry + optional `thread/read(includeTurns:true)` | Compact lane summary; optional transcript convenience. |
| `transcript` (`lane tail`) | `thread/read(includeTurns:true)` | Persisted turn/item snapshot, not a full execution log. |
| `watch` (`lane tail --follow`) | raw app-server event stream, bounded by limit/timeout | Request/response bounded sample; a true infinite tail needs a subscription control-socket extension. |
| `goal-get/set/clear` (`goal status/set/clear`) | `thread/goal/{get,set,clear}` | Native App Server goal lifecycle for owned lanes. |
| `fork` | `thread/fork` + register | Creates a new owned lane; attached source lanes remain locked until cross-process fork semantics are verified. |
| `rollback` | `thread/rollback` | Drops persisted turns only; does not revert workspace files. |
| `compact` | `thread/compact/start` | Starts App Server context compaction. |

Approvals are server→client JSON-RPC requests: while pending the lane emits `thread/status/changed` with `activeFlags:["waitingOnApproval"]`; the client replies `{id, result:{decision}}` (`accept`/`acceptForSession`/`decline`/`cancel`/…); server emits `serverRequest/resolved`. File-change approvals do NOT carry the diff — correlate by `itemId` to the `fileChange` item (`changes[].diff`) and `turn/diff/updated`.

Schema is regenerated per binary (`codex app-server generate-json-schema [--experimental]`); pin the binary and store the generated schema with the build.

## Triggers

A trigger binds **when → action → lane**, stored in the registry:
- `when`: `time` (interval or cron — cron parsed with `croniter`; we own the format and do NOT support iCal RRULE in v1), or `event` (`idle_for`, `turn_completed`, `waiting_on_approval`).
- `action`: `send(prompt)` | `steer(prompt)` | `brief(items)`.
- `guard` (optional): `idle_only`, `min_interval`, `dedupe` — and the **extension seam for future conditional triggers**.

The scheduler is **our own** (asyncio): a time wheel for time triggers + the reactor consuming the event stream for event triggers. We do not use Codex's filesystem automations (they're daemon-registered, not protocol; live pickup unconfirmed) — owning the scheduler gives full control and is why this approach was chosen.

## Lanes: owned write, attached observe-only

The daemon drives threads it spawns (`new`, backed by the lower-level `open` op) with full read/write. Existing desktop threads can be registered with `attach`, but they are **observe-only in v0**. The Phase-1 cross-process spike confirmed that a second app-server process can discover and read persisted history, but live event fan-out does not cross processes and concurrent turns are uncoordinated. Dispatch's advisory lock is dispatch-local; it cannot gate the desktop app. ADR-0005 keeps attached-lane writes locked until there is a real cross-process interlock and an explicit user opt-in.

## Approvals (v1 minimal)

The client supports the full responder loop. v1 surfaces `waiting_on_approval` as an **event trigger** (a trigger can ping a coordinator lane / the human) with a safe default decision of **decline** if no trigger handles it. A real policy engine is later.

## Tech stack

- **uv** (deps, lockfile, venv, Python-version mgmt, runner) · build backend **hatchling** · **src/ layout + PEP 420 namespace** (`src/outfitter/dispatch/`, no `__init__.py` at `outfitter/`).
- CLI: **Typer** + **Rich**. Lint/format: **Ruff**. Types: **mypy --strict**. Validation/config: **Pydantic v2** + **pydantic-settings**.
- Async: stdlib **asyncio** (subprocess + streams + unix socket server). DB: **aiosqlite** (hand-written SQL; no ORM). Logging: **structlog** (also feeds the audit log).
- MCP: the official Python **`mcp`** SDK (stdio transport first). Scheduling: small custom asyncio scheduler + `croniter` for cron (interval needs no lib). No `dateutil`/RRULE in v1.
- Tests: **pytest** + **pytest-asyncio**. Hooks: **lefthook** (polyglot; runs ruff/mypy/pytest). Task runner: **just** (justfile) for `test`/`lint`/`typecheck`/`run`. Daemon keep-alive: **launchd** LaunchAgent plist. CI: GitHub Actions + `astral-sh/setup-uv`.

## Data model (registry, SQLite)

- `lanes`: id, handle (`@name` / `→ @project:name`), role, cwd, source (`own`|`attached`), status, pinned, created_at, updated_at, last_event_at.
- `lane_sync_sources`: lane, sync state, source path/file identity, source size/mtime, parsed offsets, line count, last synced timestamp, error.
- `lane_snapshots`: lane, display name, preview, cwd, source/model/session facts, latest event timestamp, latest turn id, transcript-partial flag.
- `triggers`: id, name, lane selector, when-spec (json), action-spec (json), guard-spec (json), enabled, last_fired_at.
- `actions_log`: id, ts, lane, op, trigger_id?, request/decision, outcome — full audit of every send/action.

## Error handling / resilience

- app-server subprocess crash → daemon detects stdout EOF → restart → restore owned-lane resumes and attached-lane metadata reads → restart the reactor.
- Action on a busy lane → direct `send` starts a turn immediately; `send --queue`
  persists local queued delivery and starts one queued turn when the lane next
  becomes idle.
- Reconnect → rebuild via `thread/read` + explicit sync; rely on persisted history, not replay.
- Every action audited; per-lane advisory lock for cross-process safety.

## Testing

- Promote the existing probe scripts (`/tmp/codex_{stdio,dm,lab4,fanout}.py`) into the integration suite, run against a **real ephemeral app-server with an isolated `CODEX_HOME`** (zero pollution; `ephemeral:true` lanes).
- `test_examples(registry)` runs op examples as assertions.
- Unit: message router (canned JSONL), trigger/guard evaluation, registry, error projections.

## Rough build slices (detailed by the implementation plan)

0. **Spike:** client + ephemeral integration harness; verify cross-process two-app-server safety on a shared thread.
1. **Contract layer + registry + CLI surface:** ops for lane creation/attachment, `send`, lane reads/lists, and archive end-to-end via daemon control socket.
2. **Scheduler + reactor + triggers:** time + event, `idle_only` guard, audit log.
3. **MCP surface:** derive grouped tools from the same contracts; stdio server.
4. **Daemon lifecycle polish:** supervision, launchd plist, `up`/`down`, `status`/`log`.

(v2: remote-control surface; conditional-trigger guards; approval policy engine.)

## Open risks / questions

- **Cross-process contention** (dispatch vs desktop app-server on one thread) — resolved for v0 by ADR-0005: attached lanes are observe-only.
- **MCP transport** — stdio first; SSE/streamable-HTTP later (mirrors Codex/Trails MCP status).
- **App-server version drift** — pin the binary; the Python SDK pins an older CLI (0.132) than local (0.136), so we drive the binary directly, not via the SDK.
