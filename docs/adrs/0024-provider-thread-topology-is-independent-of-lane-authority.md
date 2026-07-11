---
id: 0024
slug: provider-thread-topology-is-independent-of-lane-authority
title: Provider Thread Topology Is Independent of Lane Authority
status: accepted
created: 2026-07-10
updated: 2026-07-10
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0024: Provider Thread Topology Is Independent of Lane Authority

## Context

Codex exposes provider-owned relationships between threads. Subagent-spawned
threads carry `parentThreadId`, while ordinary history forks carry
`forkedFromId`. Experimental `thread/list` filters can return direct children
or all spawned descendants. These relationships include threads Dispatch does
not manage and must not silently grant write authority.

Lane registration is a separate Dispatch decision. A lane records local
authority, naming, sync, and operational state; provider topology records what
Codex says exists. Conflating them would make discovery mutate authority and
would lose relationships when threads are archived, deleted, or not attached.

## Decision

Persist provider thread identity and relationships independently from lanes.

- `provider_threads` stores sparse provider observations and lifecycle
  tombstones for managed and unmanaged threads.
- `parent_thread_id` represents subagent ancestry. `forked_from_id` represents
  history derivation. They remain separate edges and forks are never presented
  as descendants.
- Managed state, Dispatch refs, handles, and lane status are joined into
  topology projections when a matching lane exists; they are not copied as
  provider truth.
- Discovery and lifecycle notifications may update provider topology without
  creating a lane, resuming a thread, or granting write authority.
- `list`, `get`, and unmanaged discovery expose bounded topology through the
  authored operations, so CLI and grouped MCP schemas derive together.
- Explicit topology refresh uses App Server's native parent/ancestor filters.
  Ordinary reads use the local cache. Missing parents, cycles, and truncation
  remain visible rather than being repaired speculatively.
- Archive and delete observations retain tombstones. They do not erase
  relationships or indexed history.

## Consequences

### Positive

- First-run discovery can show useful subagent structure without attaching
  every thread.
- Dispatch authority remains explicit and auditable.
- Parent trees survive rename, archive, delete, and lane cleanup.
- CLI and MCP consumers receive one stable topology vocabulary.
- Ordinary forks remain truthful instead of appearing as child agents.

### Tradeoffs

- Topology is an observed cache and can be incomplete or stale.
- The registry gains another migrated table and bounded graph traversal.
- Experimental App Server filters remain a compatibility risk and require
  fixture and manifest guards.
- Lifecycle tombstones require explicit future retention policy rather than
  implicit deletion.

## Alternatives considered

- **Store relationships only on lanes** - rejected because unmanaged threads
  would disappear and discovery would have to grant authority.
- **Treat forks as descendants** - rejected because the provider exposes a
  distinct history relationship with different semantics.
- **Rebuild topology on every read** - rejected because it adds App Server work
  and makes routine list/get latency depend on provider traversal.
- **Expose only raw provider fields** - rejected because consumers need bounded,
  authority-aware projections and explicit completeness signals.

## References

- [ADR-0005: Lane Authority Capability Ladder](0005-lane-authority-capability-ladder.md)
- [ADR-0011: Codex Session Registration Is Explicit](0011-codex-session-registration-is-explicit.md)
- [ADR-0017: Progressive Thread Sync Index](0017-progressive-thread-sync-index.md)
- [ADR-0023: Provider Event Log and History Index](0023-provider-event-log-and-history-index.md)
- [App Server verification](../research/app-server-verification.md)
