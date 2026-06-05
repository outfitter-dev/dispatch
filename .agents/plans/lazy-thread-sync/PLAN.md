# Lazy Thread Sync — implementation plan

Goal packet for making existing Codex thread pickup instant without eager
`thread/resume` or transcript copying. Pasteable goal: [`GOAL.md`](./GOAL.md).
Execution ledger: [`RETRO.md`](./RETRO.md). References: [`REFS.md`](./REFS.md).

## Objective

Ship progressive sync for attached Codex lanes:

- `dispatch lane attach <thread-id>` registers existing Codex threads quickly
  from compact metadata, without `thread/resume` by default.
- `dispatch lane sync <lane>` builds or refreshes dispatch's local indexed view
  with bounded top+tail parsing of Codex JSONL artifacts.
- `lane get`, `lane list`, and unmanaged discovery expose honest sync state.
- The daemon can keep attached lanes warm without whole-home indexing.
- CLI, MCP, schemas, docs, and first-party skills stay derived/aligned.

## Current Evidence

The local investigation found the right split of responsibilities:

- `thread/list(useStateDbOnly:true)` is the official cheap discovery path.
- `thread/read(includeTurns:false)` verifies a thread id and returns compact
  official metadata in a few milliseconds.
- `thread/resume` is too heavy and side-effectful for default attach: on the
  sampled thread it returned about 87 KB, emitted notifications, loaded the
  thread, and took 1-3 seconds.
- Local JSONL top+tail parsing is cheap enough for progressive sync, but must
  cap bytes as well as lines because early records can be large.
- `Thread.path` is explicitly unstable in the current App Server schema, so
  dispatch should treat it as a cached source pointer with file identity, not a
  durable id.

The packet summarizes the local notes in [`REFS.md`](./REFS.md) so execution
does not depend on chat history.

## Product Contract

Expected operator shape:

```bash
dispatch lane list --unmanaged --limit 20
dispatch lane attach <thread-id>
dispatch lane attach <thread-id> --sync
dispatch lane sync <lane>
dispatch lane sync <lane> --full
dispatch lane get <lane>
dispatch lane list
dispatch schema "lane sync"
```

Semantics:

- `attach` defaults to metadata-only and uses `thread/read(includeTurns:false)`.
- `attach --sync` performs a quick sync after registration.
- `sync` means progressive indexing, not hydration and not transcript copying.
- `sync --full` may backfill more aggressively if implemented, but should still
  be bounded and explicit; do not block the main product slice on perfect full
  archival indexing.
- Existing attached lanes remain observe-only. Do not unlock send/stop/archive
  or history writes for attached desktop lanes.
- `lane tail` may keep using official `thread/read(includeTurns:true)` for
  persisted turn summaries, but should not be described as a fast tail API.

## Implementation Slices

### Slice 1 — Codex source adapters

- Add a focused module for Codex thread metadata and JSONL source parsing.
- Wrap official App Server metadata reads/lists instead of making handlers
  inspect raw schema blobs.
- Add a JSONL top+tail parser with:
  - line and byte caps;
  - complete-line handling;
  - partial-final-line tolerance;
  - file identity capture: path, device, inode, size, mtime;
  - sanitized metadata extraction, not raw transcript dumping.
- Add fixture tests for large first records, tail offsets, partial final lines,
  missing/moved files, and path identity changes.

### Slice 2 — Registry sync index

- Bump the registry schema and add the smallest useful sync tables.
- Start with source/snapshot facts before adding item-level history.
- Candidate fields:
  - thread id, source path, device, inode, size, mtime;
  - sync state: `metadata`, `partial`, `complete`, `error`;
  - last synced at, latest event at, latest turn id, error;
  - display name, preview/excerpt policy, cwd, source, model/provider, session id;
  - top cursor/tail cursor/backfill cursor.
- Add migration/open tests, model validation tests, and older-db/newer-db checks.

### Slice 3 — Metadata-only attach and explicit sync op

- Change `attach` to verify with `thread/read(includeTurns:false)`, register the
  lane, store metadata sync state, and never call `thread/resume` by default.
- Add `sync` as a first-class op projected to CLI/MCP/schema.
- Route `dispatch lane sync` through the op registry; do not hand-write a
  special surface.
- Make idempotency and error behavior explicit:
  - missing thread -> clean App Server/not-found style error;
  - missing source file -> registered lane can still exist with sync error;
  - repeated sync -> updates snapshot/cursors.
- Update tests proving attach no longer resumes.

### Slice 4 — Read surfaces and daemon warming

- Include sync state in `lane get` and `lane list`.
- Keep `lane list --unmanaged` official and cheap, using `thread/list` state DB.
- Add bounded daemon background sync for attached lanes if it stays simple:
  - quick sync after attach when configured;
  - poll/watch attached source file size/mtime;
  - append-only parsing from stored cursor;
  - conservative defaults.
- Add config only where needed:

```toml
[sync]
quick_on_attach = true
watch_attached = true
unattached = "off" # off | cwd | recent | all
tail_lines = 200
tail_bytes = 262144
top_bytes = 262144
max_backfill_bytes_per_tick = 1048576
```

Do not implement broad automatic unattached indexing unless the slice remains
small and private by default.

### Slice 5 — Docs, skills, schemas, and local proving

- Update README/docs/usage/skills/plugin docs for the new attach/sync semantics.
- Update ADRs or add one if a durable decision changed:
  - likely: progressive sync/index cache and metadata-only attach.
- Ensure `dispatch schema "lane sync"` and MCP grouped tools expose the new op.
- Run focused tests first, then `just check`.
- Put the feature through local paces:
  - isolated registry/`DISPATCH_HOME`;
  - fixture JSONL sync;
  - real App Server metadata read if available;
  - optional real existing Codex thread attach/sync smoke in a temp dispatch
    home, read-only, no sends/stops/renames/archives.

## Review Loop

Use local review between meaningful slices:

- request/perform review with score out of 5;
- P0/P1/P2 block completion;
- P3 can be fixed if cheap or recorded in `RETRO.md`;
- update `RETRO.md` after each slice, check run, review round, and material
  decision.

Subagents are useful for:

- fixture/parser audit;
- App Server schema/current-doc check;
- docs/skill drift review;
- final code quality review.

Subagents must not commit, push, mutate PRs, merge, publish, or touch live user
agent state.

## Source Control

- Work from branch `feat/lazy-thread-sync`.
- Use Graphite if submitting PRs.
- Keep commits coherent. One commit is fine if the final diff stays reviewable;
  split only when it genuinely helps review.
- PRs stay draft until local checks and local review are clean.
- Do not merge, publish, or alter release state without explicit user approval.

## Done

Done only when:

- metadata-only attach, `lane sync`, sync index, read surfaces, and docs/skills
  are implemented or explicitly pared down with evidence;
- the code path no longer relies on eager `thread/resume` for default attach;
- schema/MCP/CLI parity tests pass;
- parser/index fixtures cover the failure modes above;
- local runtime smoke demonstrates attach/sync behavior safely;
- `just check` is green;
- local review has no unresolved P0/P1/P2;
- `RETRO.md` records final state, checks, review result, remaining risks, and
  any deferred follow-up issues.

