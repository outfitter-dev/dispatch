---
id: 0023
slug: provider-event-log-and-history-index
title: Provider Event Log and History Index
status: proposed
created: 2026-07-01
updated: 2026-07-15
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0023: Provider Event Log and History Index

## Context

Dispatch already uses SQLite as its durable registry: lanes, refs, triggers,
queued turn-delivery messages, lane sync snapshots, inbox messages,
subscriptions, model settings, and action audit records live in the local
registry database.

That database is now the normalized history and observability substrate. Codex
transcript reads still use App Server `thread/read(includeTurns:true)` as the
canonical source, but they index normalized turns, items, and references as a
side effect. Progressive sync also uses bounded App Server turn/item pages plus
compact JSONL facts to backfill recent and older managed-thread history without
copying raw transcripts wholesale or retaining raw provider payloads by default.

This is a useful v0 compromise, but it leaves important product goals awkward:

- fast history search across long-running threads;
- scriptable filtering by tool, file, date, turn, status, or provider;
- reliable message receipts and "accepted versus completed" distinctions;
- unified `needs-attention` state across approvals, elicitation, blocked turns,
  and permission prompts;
- multi-provider support where Codex App Server events and Claude hooks feed the
  same Dispatch surfaces;
- future semantic search over thread history, summaries, tool results, and
  operator notes.

Direct Claude Code 2.1.210 verification now confirms the same shape. Claude emits
structured lifecycle and attention events such as `UserPromptSubmit`,
`Notification`, `PermissionRequest`, `Elicitation`, `ElicitationResult`, `Stop`,
`StopFailure`, `SessionStart`, and `SessionEnd`. A Dispatch `UserPromptSubmit`
observation is submission evidence only. Processing requires every sibling
prompt hook to reach a terminal non-blocking outcome plus first owned-stream
assistant/tool activity. Exit 1 and timeout are fail-open, degraded-health
outcomes; exit 2 or a blocking decision prevents processing. Claude assigns a
`prompt_id` shared by `UserPromptSubmit` and repeatable `Stop` cycles. Completion
requires the final Stop hook set to settle without continuation, followed by
terminal per-message result success. Clean process exit is required when an owner
generation terminates, not for every message. Those events are not Codex
`LaneEvent`s, but they map naturally onto provider-neutral event and history
records.

We also want to investigate Turso/libSQL. Turso's current documentation describes
native vector search, local and cloud-capable SQLite-compatible storage, and newer
SDKs such as `pyturso` for local embedded use. That is promising for future
semantic search, multi-machine sync, and concurrent write scenarios, but it should
not force the core schema or make the local-only Dispatch path depend on a new
storage engine before the event model is proven.

## Decision

Build a provider-neutral event log and normalized history index, starting with
Codex as the first producer and dogfood target.

The first implementation should keep SQLite/`aiosqlite` as the default local
backend. Turso/libSQL is an explicit spike behind a storage boundary, not a
required dependency for the initial substrate.

Introduce storage concepts along these lines:

- `provider_events` — append-only raw-ish provider event records. Each record has
  provider, provider thread/session id, optional Dispatch lane ref/id, event type,
  provider timestamp when available, Dispatch receive timestamp, correlation ids,
  compact indexed fields, and retained raw payload according to policy.
- `thread_turns` — normalized turn lifecycle facts: turn id, provider, thread id,
  lane id, status, started/completed/failed timestamps, error fields, and
  completion source.
- `thread_items` — normalized transcript/history items: messages, tool calls,
  file changes, approvals, goals, compaction summaries, and provider-specific
  items represented through a stable common shape.
- `thread_item_refs` — extracted queryable references from items: file paths,
  tools, commands, error classes, related message ids, and other structured refs.
- `message_receipts` — Dispatch-originated message lifecycle: created, sent to
  transport, accepted by provider, completed, failed, or timed out.
- `lane_runtime_state` — derived compact state used by `list`, `status`, `get`,
  `watch`, subscriptions, and triggers.
- `provider_capacity_observations` — one replace-in-place, provider-neutral
  account/capacity observation per provider, host scope, and config scope. It
  stores normalized windows, reset-credit summaries, historical usage, source,
  freshness, and confidence without raw auth responses or mutation handles.

The existing `lanes`, `lane_sync_sources`, `lane_snapshots`, `queued_messages`,
`inbox_messages`, `subscriptions`, `triggers`, and `actions_log` tables remain
valid. They should be migrated toward reading from the new substrate where that
reduces duplication or makes state more truthful.

Codex is first:

1. Persist normalized Codex App Server events into `provider_events`.
2. Reduce those events into `thread_turns`, `message_receipts`, and
   `lane_runtime_state`.
3. Backfill `thread_turns` and `thread_items` from
   `thread/read(includeTurns:true)` for managed threads.
   Live item notifications and history replay must pass through one canonical
   item adapter so normalized rows and refs converge regardless of arrival
   order. Unknown provider item types remain visible rather than being dropped.
   Provider history reads are additive because Codex can omit transient tool
   items that were delivered live; absence from replay is not deletion proof.
   Normalized searchable fields are bounded and credential-redacted separately
   from raw-payload retention. Retention is monotonic during ingestion; purging
   retained payloads requires a future explicit retention operation.
4. Persist Codex thread lifecycle events such as `thread/archived` and
   `thread/unarchived`, and reconcile managed-lane archive state from App
   Server list membership.
5. Use Codex JSONL sync as a cheap discovery and progressive backfill source,
   keeping source offsets and file identity so indexing can resume efficiently.
6. Move `history`, `search`, `tail`, `list`, `get`, subscriptions, and trigger
   predicates toward DB-backed reads where correctness and freshness are
   sufficient.

Claude comes second:

1. Dispatch-created Claude sessions add per-session hooks where possible rather
   than mutating global Claude settings.
2. Claude hook events are ingested into `provider_events`.
3. Hook observations are combined with owned structured stream events before
   reduction into the same `message_receipts`,
   `lane_runtime_state`, and attention/inbox semantics used for Codex.
4. The preferred human-coexistent transport is a pinned, hardened zmx-hosted
   Agent View cockpit using Claude's guarded quick-reply path. It remains blocked
   behind DIS-54 until one live proof establishes a single coherent history,
   target-safe revisioned input, and correlated aggregate receipts.
5. The verified headless fallback is one exclusive persistent stream-JSON owner.
   A fresh resume process may replace it only after proven owner exit; it is not
   a per-turn transport and does not claim transparent human coexistence.
6. zmx 0.6.0 as shipped is excluded because raw sends are not acknowledged and
   PTY input bytes are always logged. DIS-54 may admit a pinned hardened build
   with revisioned snapshots, named keys, conditional atomic serialized writes,
   an automation lease, explicit loss signaling, and input-log redaction. A PTY
   write acknowledgement is transport evidence, never a Claude receipt.

Storage backend policy:

- Keep the default embedded local backend boring and file-based.
- Define a small storage boundary before introducing Turso/libSQL.
- Run the same fixture suite against the default SQLite backend and any Turso
  spike backend.
- Treat vector search as an optional index over normalized items and summaries,
  not as the primary source of truth.

2026-07-02 spike result: keep SQLite/`aiosqlite` as the default. The Turso/libSQL
storage spike proved representative registry/history SQL can run on stdlib
SQLite, `pyturso`, and `libsql` after a small provider-event upsert portability
hardening, but it did not justify a default backend migration. Current Python
Turso/libSQL package probes are synchronous from Dispatch's perspective, full
registry test parity still needs a smaller connection/transaction boundary, and
Turso Sync/cloud use requires a separate security/config decision.

Retention and privacy policy:

- Retain normalized indexed fields by default.
- Make raw provider payload retention configurable.
- Avoid enabling full tool-call payload capture, message-display streaming, or
  large raw transcript retention by default until privacy and storage costs are
  explicit.
- Keep provider source artifacts as the recoverable source of truth where
  available; the Dispatch DB is a local query/index layer plus operational state.
- Treat archive state as provider lifecycle metadata, not cleanup permission.
  `archive`, `restore`, event indexing, and sync reconciliation must not delete
  append-only provider events. Any future cleanup of bulky raw payloads or derived
  caches must be explicit, auditable, and preserve enough metadata to re-sync
  when provider/source artifacts still exist.

## Consequences

### Positive

- Dispatch gains a real local observability and history substrate instead of
  reparsing whole provider histories at command time.
- Codex becomes the clean proving ground before the messier Claude backend depends
  on the design.
- CLI, MCP, triggers, inbox, subscriptions, and future remote surfaces can read
  the same derived state.
- Message receipts can distinguish possible transport submission, observed
  submission, provider processing, Stop cycles, and structurally completed turns.
- `needs-attention` can become a normalized state across Codex approvals and
  Claude permission/elicitation hooks.
- History search can become fast, scriptable, and filterable by stable fields.
- The schema creates a natural place for future semantic/vector search without a
  separate vector database.
- Provider-specific volatility is isolated in ingestion adapters and reducers.

### Tradeoffs

- The registry becomes more than a small lane/trigger database; migrations,
  retention, and corruption recovery become more important.
- Reducers must be idempotent so repeated live events, daemon restarts, and
  backfills do not duplicate turns or items.
- The local index can be stale or partial; surfaces must expose freshness and
  sync state honestly.
- Raw payload retention creates privacy and storage risk if defaults are too
  broad.
- Turso/libSQL may add useful capabilities but also adds dependency, packaging,
  compatibility, and beta-feature risk if adopted too early.

## Alternatives considered

- **Keep App Server reads as the only history surface** — rejected: it preserves
  canonical reads but keeps history slow, hard to filter, and provider-specific.
- **Copy full transcripts into Dispatch immediately** — rejected: expensive,
  privacy-sensitive, and unnecessary for first-run usability. Normalize and index
  selected fields first; backfill progressively.
- **Design around Claude hooks first** — rejected: Claude's transport is less
  stable and more shell/PTY-shaped. Codex has cleaner semantics and should prove
  the substrate first.
- **Migrate the whole registry to Turso now** — rejected: the schema and
  ingestion contracts matter more than the engine. Turso should be evaluated
  behind a storage boundary.
- **Use a separate vector database** — rejected for now: normalized SQL history
  should come first, and Turso/libSQL may provide enough vector search in the same
  local database later.
- **Expose raw provider events directly to users and triggers** — rejected:
  surfaces should operate on normalized events and indexed fields. Raw payloads
  are for debugging, replay, and adapter development.

## References

- [ADR-0007: Normalized Internal LaneEvent Vocabulary](0007-normalized-internal-lane-events.md)
- [ADR-0012: Conditional Triggers and Event Sinks](0012-conditional-triggers-and-event-sinks.md)
- [ADR-0016: History, Goals, and Bounded Watch](0016-history-goals-and-bounded-watch.md)
- [ADR-0017: Progressive Thread Sync Index](0017-progressive-thread-sync-index.md)
- [ADR-0020: Live-Use Trust Contracts](0020-live-use-trust-contracts.md)
- [ADR-0021: Lane Inbox and Delivery](0021-lane-inbox-and-delivery.md)
- [ADR-0022: Event Subscriptions](0022-event-subscriptions.md)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [ADR-0026: Claude Control Uses Resume Processes and Hooks](0026-claude-control-uses-resume-processes-and-hooks.md)
- [Turso AI and embeddings](https://docs.turso.tech/features/ai-and-embeddings)
- [Turso libSQL overview](https://docs.turso.tech/libsql)
- [Turso Python quickstart](https://docs.turso.tech/sdk/python/quickstart)
- [Turso/libSQL storage spike](../research/turso-libsql-storage-spike.md)
