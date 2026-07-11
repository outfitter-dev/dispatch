---
id: 0017
slug: progressive-thread-sync-index
title: Progressive Thread Sync Index
status: accepted
created: 2026-06-05
updated: 2026-07-11
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0017: Progressive Thread Sync Index

## Context

dispatch can discover existing Codex desktop threads through App Server
`thread/list(useStateDbOnly:true)` and can read compact thread metadata through
`thread/read(includeTurns:false)`. That is enough to make first pickup feel fast
and honest.

The previous attach shape leaned on `thread/resume`, which is too heavy for a
default registration path. Resume can load persisted turns, can be slow on long
threads, and suggests a live co-presence model that ADR-0005 explicitly does not
grant for desktop-vs-dispatch app-server processes.

At the same time, operators need more than a raw thread id. They need enough
local state to identify, list, and inspect attached lanes without copying every
transcript into dispatch up front.

## Decision

Attach is metadata-only by default:

- `dispatch attach <thread-id>` verifies the id with
  `thread/read(includeTurns:false)`.
- It registers a turn-write locked attached lane and stores metadata sync state.
- It does not call `thread/resume`, load turn history, or grant write authority.

Progressive sync is explicit:

- `dispatch attach <thread-id> --sync` runs a quick sync after registration.
- `dispatch sync <lane>` refreshes dispatch's local indexed view.
- `dispatch sync <lane> --full` scans the current source from the beginning,
  still bounded by `--max-bytes`; oversized sources remain explicitly partial.

The sync index is a compact SQLite cache:

- `lane_sync_sources` records sync state, source path, file identity, size/mtime,
  complete-line continuation offsets, App Server turn/item cursors, capability,
  completion/truncation state, per-run counts, duration, last sync time, and errors.
- `lane_snapshots` records display name, preview, cwd, source/model/session facts,
  latest event timestamp, latest turn id, and whether the transcript view is
  partial.
- Sync resumes the thread with `excludeTurns:true`, bootstraps one recent turn
  through `initialTurnsPage`, and hydrates that turn through bounded item pages.
  Repeated calls use an anchor-inclusive ascending cursor to reconcile newer turns
  first, then continue newest-to-oldest backfill from the durable older cursor.
  Cursor progress and a bounded cycle guard are written only after additive
  indexing, so replay after a crash is safe and malformed provider cursor loops
  fail closed across daemon restarts.
- Quick sync also reads bounded top+tail JSONL records when App Server exposes a
  rollout path. Unchanged files read zero bytes; same-inode appends continue from
  the last complete line; truncation or rotation resets to bounded top+tail.
- Experimental paging is capability-gated. Older binaries retain metadata and
  JSONL sync with `history_capability=unsupported` rather than silently issuing an
  unbounded history read. If turn paging works but item paging is unavailable,
  `turn-page-fallback` re-fetches the exact pending turn with full items. An atomic
  turn larger than the configured persistence budget remains pending/truncated
  until the operator explicitly raises that budget; Dispatch does not overrun the
  database bound. The same pre-persistence byte check applies to native item pages.
- The byte budget is aggregate across local and provider history and is checked
  between provider pages. Observed bytes may exceed it by one received page, but
  a page that exceeds the remaining persistence budget is not indexed.
- Daemon restart metadata-resumes owned lanes and attached lanes whose durable
  sync state proves explicit live observation was established. Plain attached
  registration remains metadata-read only and never becomes an implicit resume.
- Partial sync does not promise exact whole-file counts. Exact counts are a
  full-scan property.

`dispatch tail` remains the explicit history surface and continues to use official
App Server `thread/read(includeTurns:true)` persisted history.

## Consequences

### Positive

- First attach is cheap and side-effect-minimal.
- Long-lived threads can be picked up immediately, then backfilled when useful.
- List/get surfaces can show sync state without needing a transcript read.
- Attached-lane authority remains aligned with ADR-0005.

### Tradeoffs

- The sync cache can be partial or stale; surfaces must expose sync state honestly.
- JSONL rollout paths and payload shapes are Codex implementation details. The
  parser must be conservative and tolerate missing files, invalid lines, and
  partial final writes.
- `--full` is intentionally more expensive and should remain opt-in.

## Alternatives considered

- **Eager `thread/resume` on plain attach** — rejected. Plain attach remains
  metadata-only; explicit `attach --sync` and `sync` opt into metadata-only live
  observation plus bounded history continuation.
- **Copy full transcripts into dispatch by default** — rejected: bad first-run
  latency and unnecessary data duplication.
- **Only use App Server history reads, no local index** — rejected: keeps attach
  simple but leaves list/get surfaces without cheap recency and identity facts.
- **Automatically sync every unattached Codex thread** — rejected for v0: it is
  surprising, potentially expensive, and conflicts with ADR-0011's explicit
  registration policy.

## References

- ADR-0005 (Lane Authority Capability Ladder)
- ADR-0011 (Codex Session Registration Is Explicit)
- ADR-0016 (History, Goals, and Bounded Watch)
- `docs/research/app-server-verification.md`
- `.agents/plans/lazy-thread-sync/PLAN.md`
