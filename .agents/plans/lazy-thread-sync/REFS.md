# Lazy Thread Sync — references

## Local Investigation Summary

The local investigation note is gitignored at
`.agents/notes/2026-06-05-codex-storage-investigation.md`. This file captures
the load-bearing findings so the goal packet does not depend on chat history or
ignored notes.

Evidence gathered on 2026-06-05:

- Codex CLI: `codex-cli 0.137.0-alpha.4`.
- dispatch CLI: `dispatch 0.2.1`.
- Sample thread id: `019e9598-9214-7ed1-ac40-52d6d675d3e7`.
- Sample JSONL artifact size: about 1.49 MB, 812 records.
- `~/.codex` symlinked to `/Users/mg/.config/codex`.

Observed stores:

- `session_index.jsonl`: display thread names and updated timestamps.
- `state_5.sqlite`: thread metadata, rollout path, cwd, title/preview,
  model/provider, archive state, dynamic tools, spawn edges, agent jobs.
- `goals_1.sqlite`: native goals.
- `logs_2.sqlite`: logs/telemetry, not transcript structure.
- `sessions/YYYY/MM/DD/rollout-...<thread-id>.jsonl`: append-oriented JSONL.
- `shell_snapshots/<thread-id>.<timestamp>.sh`: shell snapshots.

Important mismatch:

- `state_5.sqlite.threads.title` and `preview` can be the raw opening prompt.
- `session_index.jsonl.thread_name` and App Server `Thread.name` are better
  user-facing display-name sources.

Schema findings from `codex app-server generate-json-schema --out <tmp>`:

- `thread/list` supports `useStateDbOnly`, pagination, cwd/search/source/model
  filters, and returns rows under `result.data`.
- `thread/read` only accepts `threadId` and `includeTurns`.
- `thread/resume` does not currently expose a usable turn-page parameter.
- `Thread.path` is marked `[UNSTABLE]`; store path plus file identity as a
  pointer, not the durable id.
- `Thread.turns` is populated only for resume/fork/rollback/read with
  `includeTurns:true`.

Timing on the sample thread:

- `thread/list(useStateDbOnly:true, limit=10)`: about 10 ms, about 33.5 KB.
- `thread/list(useStateDbOnly:false, limit=10)`: about 133 ms.
- `thread/read(includeTurns:false)`: about 3 ms, about 4 KB.
- `thread/read(includeTurns:true)`: about 10-15 ms, about 86 KB.
- `thread/resume`: about 1.1-3.0 seconds, about 87 KB, loaded the thread and
  emitted notifications.
- local first 8 JSONL records: about 0.46 ms, about 114 KB.
- local tail 256 KiB window: about 0.88 ms, 126 complete records.
- local full 1.49 MB parse: about 5 ms.

JSONL shape on the sample:

- top-level types: `response_item`, `event_msg`, `session_meta`, `turn_context`.
- useful payload kinds: `thread_goal_updated`, `function_call`,
  `function_call_output`, `token_count`, `message`, `agent_message`,
  `reasoning`, `custom_tool_call`, `custom_tool_call_output`,
  `patch_apply_end`, `task_complete`, `user_message`.
- first `session_meta` starts at byte 0 and includes thread id, cwd, source,
  thread source, model provider, cli version, dynamic tools, and git keys.
- `turn_context` includes model, effort, approval policy, sandbox policy, cwd.
- `task_complete` includes duration, turn id, completion timestamp, and final
  message length.

## Repo References

- `AGENTS.md`
- `.agents/plans/PLANNING.md`
- `docs/development/design.md`
- `docs/adrs/0005-lane-authority-capability-ladder.md`
- `docs/adrs/0011-codex-session-registration-is-explicit.md`
- `docs/adrs/0016-history-goals-and-bounded-watch.md`
- `docs/usage/README.md`
- `skills/dispatch/SKILL.md`
- `skills/dm/SKILL.md`
- `src/outfitter/dispatch/core/handlers.py`
- `src/outfitter/dispatch/core/models.py`
- `src/outfitter/dispatch/core/ops.py`
- `src/outfitter/dispatch/client/client.py`
- `src/outfitter/dispatch/client/models.py`
- `src/outfitter/dispatch/registry/store.py`
- `src/outfitter/dispatch/contracts/derive_cli.py`
- `src/outfitter/dispatch/contracts/derive_mcp.py`
- `tests/core/test_handlers.py`
- `tests/client/test_client.py`
- `tests/surfaces/test_parity.py`
- `tests/surfaces/test_mcp_routing.py`

## Follow-Up Unknowns

- Active-write behavior: verify JSONL append behavior while a thread is running.
- Rename propagation: verify display-name update order across App Server,
  `session_index.jsonl`, state DB, and JSONL.
- Archived threads: verify `thread/list(archived:true)` behavior.
- Subagent source projection: state DB source can be JSON-shaped strings.
- Rotation/rewrite: detect file shrink, inode change, move, or compact.
- Privacy policy: decide whether excerpts are stored by default, capped, or
  opt-in.

