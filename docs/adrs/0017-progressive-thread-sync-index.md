---
id: 0017
slug: progressive-thread-sync-index
title: Progressive Thread Sync Index
status: accepted
created: 2026-06-05
updated: 2026-06-05
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

- `dispatch lane attach <thread-id>` verifies the id with
  `thread/read(includeTurns:false)`.
- It registers an observe-only attached lane and stores metadata sync state.
- It does not call `thread/resume`, load turn history, or grant write authority.

Progressive sync is explicit:

- `dispatch lane attach <thread-id> --sync` runs a quick sync after registration.
- `dispatch lane sync <lane>` refreshes dispatch's local indexed view.
- `dispatch lane sync <lane> --full` scans the whole current source file and marks
  that cache complete for the current file identity.

The sync index is a compact SQLite cache:

- `lane_sync_sources` records sync state, source path, file identity, size/mtime,
  parsed offsets, line count when known, last sync time, and errors.
- `lane_snapshots` records display name, preview, cwd, source/model/session facts,
  latest event timestamp, latest turn id, and whether the transcript view is
  partial.
- Quick sync reads bounded top+tail JSONL records from Codex's local rollout path
  when App Server exposes one. It captures early metadata plus recent state, then
  can be backfilled later.
- Partial sync does not promise exact whole-file counts. Exact counts are a
  full-scan property.

`lane tail` remains the explicit history surface and continues to use official
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

- **Eager `thread/resume` on attach** — rejected: slower, heavier, and implies a
  co-presence model we do not have across desktop and dispatch app-server
  processes.
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
