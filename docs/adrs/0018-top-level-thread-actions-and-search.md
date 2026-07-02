---
id: 0018
slug: top-level-thread-actions-and-search
title: Top-Level Thread Actions and Search
status: accepted
created: 2026-06-05
updated: 2026-06-05
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0018: Top-Level Thread Actions and Search

## Context

dispatch originally required a Codex thread to be registered as a lane before most
operator workflows felt natural. That made the first-run experience clunky: a user could
see existing Codex sessions through `lane list --unmanaged`, but still had to attach before
basic lifecycle cleanup or targeted inspection.

At the same time, ADR-0005 correctly blocks turn-writing and history-mutating operations
on attached lanes because dispatch and the desktop app do not share a cross-process write
interlock. We need better thread ergonomics without weakening that authority boundary.

The App Server exposes stable metadata/lifecycle methods (`thread/name/set`,
`thread/archive`, `thread/unarchive`) and an experimental broad search method
(`thread/search`). The experimental search method can search persisted threads, but it
does not provide all dispatch-facing filters directly.

## Decision

Use three explicit states:

- **Managed**: a thread registered in dispatch's registry, either owned or attached.
- **Unmanaged**: a persisted Codex thread visible to App Server but not registered in
  dispatch.
- **Synced**: a managed lane whose local dispatch index has been refreshed. Sync is
  separate from management and separate from App Server thread lifecycle.

Expose `rename`, `archive`, `restore`, and `search` at the top level, while keeping lane
group variants for lane-shaped workflows:

- `dispatch rename <target> <new>` and `dispatch lane rename <old> <new>`
- `dispatch archive <target>` and `dispatch lane archive <target>`
- `dispatch restore <target>` and `dispatch lane restore <target>`
- `dispatch search <query>` and `dispatch lane search <lane> <query>`

Targets may be a managed lane id, a managed `@handle`, or a raw unmanaged Codex thread id.
An unresolved `@handle` is a missing lane, not a raw thread id fallback. Raw thread ids keep
the first-run path available without silently reinterpreting human handles.

`restore` only calls `thread/unarchive`; it must not resume the thread, start a turn, or
drain queued work.

Broad `search` uses experimental App Server `thread/search`, then applies dispatch-side
filters for managed/unmanaged state, repo/directory containment, and date ranges. Focused
lane search uses `thread/read(includeTurns:true)` and a local substring scan because the
App Server search schema does not expose a thread-id filter.

2026-07-02 update: the explicit managed-history path moved to `dispatch query`.
`query` reads Dispatch's normalized `thread_items`/`thread_item_refs` index and does
not call App Server search. Broad `search` remains App Server-backed.

## Consequences

### Positive

- Users can clean up and inspect existing Codex threads before deciding whether to attach.
- The managed/unmanaged/synced vocabulary matches the real state model and avoids implying
  that sync grants authority.
- Attached lanes remain protected from turn-writing and history-mutating operations while
  still supporting explicit metadata/lifecycle actions.
- CLI and MCP continue to derive from the same ops; top-level commands are ergonomic routes,
  not separate behavior.

### Negative

- Broad search depends on an experimental App Server method. It must stay documented as
  experimental and covered by schema/client tests.
- Repo, directory, date, and managed/unmanaged filters are dispatch-side filters, so
  `--max-scan` can bound results before every possible match is examined.
- Unmanaged raw-id actions rely on App Server errors for nonexistent raw ids.

## Alternatives Considered

- **Require attach before rename/archive/restore/search** — rejected: it preserves the old
  friction and makes first-run cleanup unnecessarily indirect.
- **Treat sync as attach/hydrate/management** — rejected: sync is only an index refresh and
  should not imply write authority or ownership.
- **Use `thread/search` for lane-focused search too** — rejected: the current experimental
  schema does not provide a thread-id filter, so local transcript scan is more precise.
- **Build a full local transcript database first** — rejected for this slice: progressive
  sync already captures compact local facts, and broad search can start from App Server
  search without an ingestion-heavy first-run path.

## References

- ADR-0005 (Lane Authority Capability Ladder)
- ADR-0016 (History, Goals, and Bounded Watch)
- ADR-0017 (Progressive Thread Sync Index)
- `docs/research/app-server-verification.md` (`thread/name/set`, `thread/archive`,
  `thread/unarchive`, experimental `thread/search`)
