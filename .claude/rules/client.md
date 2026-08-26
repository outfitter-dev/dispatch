# client/ — the App Server client

Path: `src/outfitter/dispatch/client/`. The ONLY place that spawns or speaks to `codex app-server`. Importable standalone (no daemon dependency).

## Transport

- Default to spawning `codex app-server --listen stdio://` via `asyncio.create_subprocess_exec`. **stdio JSONL is the only bare-JSON transport** — one newline-delimited JSON message per line.
- An explicit absolute `[app_server].socket_path` / `DISPATCH_APP_SERVER_SOCKET` may attach to an existing local App Server. Unix transport is WebSocket-framed; use a real WebSocket client. Dispatch owns only that connection and must never stop or unlink the shared server. Do not silently fall back to stdio when an explicit socket fails.
- One app-server process hosts many lanes. A single connection multiplexes them.
- Detect stdio EOF or socket disconnect; surface it so the daemon can restart/reconnect + re-resume.

## Message router

Demux the single stream: responses by request `id`, notifications by `threadId`, into per-lane async queues + a global stream. Expose `events(thread_id | all)`. This is the verified pattern (mirrors the Python SDK's internal router).

## Primitives (typed; Pydantic wire models)

`initialize` → `thread_start/resume/list/read/archive/unarchive/name-set/search` → `turn_start/steer/interrupt` → `inject_items` → approval responder. Verified gotchas to encode:

- `thread/start.sandbox` is a **string** enum (`read-only`/`workspace-write`/`danger-full-access`); `turn/start.sandboxPolicy` is an **object** (`{type:"readOnly", ...}`). Different encodings — model both.
- `turn/steer` requires `expectedTurnId` (from `turn/started`).
- `thread/list` results are under `result.data` (not `result.threads`); `useStateDbOnly:true` reads the persisted store.
- Current `thread/list` supports native `archived`, `cwd`, `searchTerm`, `sourceKinds`, and sort filters; use them when they match dispatch semantics, then keep registry/authority filters in core.
- `thread/search` is experimental; enable the experimental API capability before using it and keep the wrapper thin.
- `thread/resume` of a *persisted* thread yields live event fan-out; pre-persistence it errors `no rollout found`.
- Approvals are server→client requests: lane emits `thread/status/changed` `activeFlags:["waitingOnApproval"]`; reply `{id, result:{decision}}` (`accept`/`acceptForSession`/`decline`/`cancel`); server emits `serverRequest/resolved`. File-change approvals carry **no diff** — correlate by `itemId` to the `fileChange` item.
- Threads persist by default (`ephemeral:false`). Pass `ephemeral:true` for throwaway/test lanes.

## Discipline

- Pin/record the binary; regenerate wire models from `codex app-server generate-json-schema` for that version. Do not assume the `openai-codex` Python SDK matches the installed CLI; it has lagged before.
- No business logic here — this layer is transport + typed primitives only. Orchestration lives in `core/`.
