# Lazy Thread Sync — execution ledger

This file must be kept current by the goal executor. Do not claim completion
until the final state is recorded here.

## Starting State

- Branch: `feat/lazy-thread-sync`
- Base: `main`
- Objective: implement metadata-only attach and explicit progressive sync for
  existing Codex threads.
- Current status: implemented locally; checks and smoke are green.

## Execution Log

- 2026-06-05: Packet created from local Codex storage investigation.
- 2026-06-05: Implemented metadata-only attach using
  `thread/read(includeTurns:false)`, added explicit `sync` op, and projected
  `dispatch lane sync <lane>` through CLI/MCP/schema.
- 2026-06-05: Added registry schema v2 tables `lane_sync_sources` and
  `lane_snapshots`.
- 2026-06-05: Added bounded Codex JSONL top+tail sync scanner and full-scan mode.
- 2026-06-05: Updated daemon restart behavior so owned lanes resume but attached
  lanes stay metadata-only.
- 2026-06-05: Updated README, usage docs, design doc, dispatch skill, ADR-0002,
  ADR-0005, ADR-0009, and added ADR-0017.

## Decisions And Divergence

- Quick sync does not report an exact whole-file `line_count`; that would require
  walking the whole JSONL file and would defeat the fast-path goal. Full sync
  reports `line_count`.
- Supervisor restart no longer resumes attached lanes. It now resumes owned lanes
  and performs metadata reads for attached lanes.

## Verification Log

- `uv run pytest tests/core/test_sync.py tests/registry/test_store.py tests/test_doctor.py -q`
  -> 15 passed.
- `uv run pytest tests/core/test_sync.py tests/registry/test_store.py tests/core/test_handlers.py tests/client/test_models.py tests/surfaces/test_parity.py tests/surfaces/test_mcp_routing.py tests/test_doctor.py -q`
  -> 74 passed.
- `uv run pytest tests/core/test_sync.py tests/registry/test_store.py tests/core/test_handlers.py tests/daemon/test_supervisor.py tests/client/test_models.py tests/surfaces/test_parity.py tests/surfaces/test_mcp_routing.py tests/test_doctor.py -q`
  -> 76 passed.
- `uv run ruff check src tests` -> passed.
- `uv run mypy src tests` -> passed.
- `uv run pytest tests/registry/test_store.py tests/core/test_handlers.py -q`
  -> 49 passed.
- `uv run pytest tests/core/test_sync.py tests/core/test_handlers.py tests/registry/test_store.py -q`
  -> 52 passed.
- `just check` -> ruff passed, format check passed, mypy passed, pytest 164
  passed / 8 deselected, `uv build` succeeded, package contents check
  succeeded.
- Seeded stale `dist/stale-review-loop.whl` and
  `dist/stale-review-loop.tar.gz`, then ran `just check` -> ruff passed, format
  check passed, mypy passed, pytest 167 passed / 8 deselected, stale artifacts
  removed before build, `outfitter_dispatch-0.3.0` wheel/sdist built, package
  contents check succeeded.
- `uv run dispatch schema "lane sync" | jq -r '.op, (.input.properties | keys | join(",")), .output.title'`
  -> `sync`, `full,lane`, `LaneSyncResult`.
- `uv run dispatch lane sync --help` -> showed required `LANE`, `--full`,
  `--json`.
- `uv run dispatch lane attach --help` -> showed required `THREAD`, `--sync`,
  `--json`.
- Safe runtime smoke with `DISPATCH_HOME=/tmp/dispatch-lazy-sync.qfYNZH`:
  `dispatch up`, `lane list --unmanaged --limit 3 --json`, `lane attach <id>
  --sync --json`, `lane get <id> --json`, `lane list --json`, `lane sync <id>
  --json`, `doctor --no-app-server --json`, `dispatch down`. Result: temp
  daemon started/stopped; unmanaged discovery saw persisted sessions; attach
  registered one observe-only attached lane; sync state surfaced as `partial`
  with source path, source size, latest event timestamp, latest turn id, and no
  sync error; registry schema version was 2 with both new tables.
- Post-review safe runtime smoke with temp `DISPATCH_HOME`: `dispatch up`,
  `lane list --unmanaged --limit 1 --json`, `lane attach <id> --sync --json`,
  `lane get <id> --json`, `lane sync <id> --json`, `dispatch down`. Result:
  attach/sync succeeded against a real persisted Codex thread with a 2.6 MB
  JSONL source; sync state surfaced as `partial` with no sync error.

## Local Review Log

- P2 found and fixed: quick scan initially walked the full file to count lines,
  defeating the long-thread performance goal. Fixed by making partial sync line
  count unknown and preserving exact counts for `--full`.
- P2 found and fixed: daemon supervisor still resumed attached lanes after
  app-server restart. Fixed by restoring attached lanes with metadata reads only.
- P2/P3 challenge resolved: attach initially persisted lane, sync metadata, and
  audit in separate registry calls. Fixed by adding one registry transaction for
  lane registration, initial sync state, and attach audit, with rollback coverage.
- P3 found and fixed: malformed `thread/read` metadata could have surfaced as a
  generic internal error. Fixed by projecting invalid metadata as an
  `app_server` error with a regression test.
- P2 found and fixed in final review loop: JSONL scanning was synchronous disk
  I/O inside async handlers. Fixed by running scanner work through
  `asyncio.to_thread`.
- P2 found and fixed in final review loop: the top-window parser used line
  iteration, so a single huge first JSONL line could exceed the byte cap. Fixed
  by reading a fixed byte window and ignoring incomplete trailing records.
- P3 found and fixed in final review loop: stale local `dist/` artifacts could
  make `just check` fail after a version bump. Fixed by cleaning wheel/sdist
  artifacts before `uv build` in the check recipe.

## PR / Source-Control State

- Draft PR: https://github.com/outfitter-dev/dispatch/pull/31
- Branch pushed through Graphite: `feat/lazy-thread-sync`.
- No merge/publish/release action yet.

## Forbidden-Action Audit

- No attached-lane write authority unlocked without explicit approval.
- No live Codex send/stop/rename/archive performed during smoke testing.
- No broad whole-home indexing enabled by default.
- No transcript bulk copy introduced by default.
- No merge/publish/release mutation without explicit approval.
- No secrets committed.
- Temp `DISPATCH_HOME` created for smoke was stopped and removed.

## Remaining Risks / Follow-Ups

- Active-write JSONL append behavior still needs proof.
- Rename propagation still needs proof.
- Archived-thread behavior still needs proof.
- Subagent source projection still needs proof.
- File rotation/rewrite behavior still needs proof.
- Excerpt privacy policy must be explicit if future sync stores transcript
  excerpts. This slice stores preview and compact facts only.

## Final State

Complete locally and submitted as draft PR #31.
