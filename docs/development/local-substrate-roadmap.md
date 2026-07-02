# Local Substrate Roadmap

Status: active planning note
Date: 2026-07-02

Dispatch has crossed from "could Turso/libSQL help someday?" into "the product
we want needs a stronger local substrate." The pressure is concrete:

- multi-machine Dispatch, where multiple local daemons can coordinate work
  across machines, accounts, and workspaces;
- semantic search over long-lived agent history, decisions, failures, files,
  tools, goals, and handoffs;
- concurrent event ingestion from Codex, Claude hooks, subscriptions, receipts,
  triggers, debug capture, and future remote deliveries;
- a clean Cloud Gateway boundary where the gateway routes intent and policy but
  does not become the default sink for agent logs.

The answer is not "migrate the registry to Turso immediately." The answer is to
shape Dispatch so SQLite remains a boring default while Turso/libSQL can become
an optional or preferred backend for the parts of the product that earn it.

## Product Thesis

The long-term substrate is local-first and selected-sync:

- Each machine owns its local Dispatch daemon, provider accounts, filesystem,
  approvals, and detailed operational history.
- Dispatch stores normalized local history and operational state in a durable
  local database.
- Machines may sync selected compact state when multi-machine coordination needs
  it.
- Raw provider payloads, full transcripts, debug captures, secrets, and large
  tool outputs do not leave the local machine by default.
- The Cloud Gateway handles external ingress, routing, policy, pairing, machine
  presence, and delivery coordination. It is not a cloud `dispatchd` and not a
  default agent-log archive.

This preserves the local authority model while still making Dispatch useful
from Slack, Linear, another laptop, or a remote machine.

## Existing Anchors

- [ADR-0013: Dispatch Mesh Is Daemon Federation](../adrs/0013-dispatch-mesh-is-daemon-federation.md)
  says multi-machine coordination federates Dispatch daemons, not Codex App
  Servers.
- [ADR-0014: Mesh Auth, Discovery, and Durable Queues](../adrs/0014-mesh-auth-discovery-and-durable-queues.md)
  says remote delivery needs durable queues, idempotency, pairing, and
  capability checks.
- [ADR-0023: Provider Event Log and History Index](../adrs/0023-provider-event-log-and-history-index.md)
  establishes the provider-neutral event/history substrate.
- [Turso/libSQL Storage Spike](../research/turso-libsql-storage-spike.md)
  proves representative registry/history SQL can run on SQLite, `pyturso`, and
  `libsql` after a small portability hardening, while keeping SQLite as the
  default.
- [Dispatch Cloud Gateway](cloud-gateway.md) sketches the always-on ingress and
  routing plane.

## Stack Shape

### 1. Storage Boundary

The next storage step is a small connection/transaction boundary, not an ORM and
not a wholesale `Registry` interface split.

The boundary should let Dispatch answer these questions with tests:

- Can the default SQLite/`aiosqlite` path still pass the existing registry
  behavior suite?
- Can selected contract tests run through a DB-API-like adapter against
  `pyturso` or `libsql`?
- Which SQL forms are portable enough for Dispatch's future?
- Where do async daemon semantics require a dedicated writer thread,
  `run_in_executor`, or a different package path?

The boundary should be justified by actual call sites. If it starts to look like
a framework, it is probably too broad.

### 2. Concurrent Event Ingestion

Dispatch should measure event ingestion before changing engines.

The harness should use synthetic provider events and history items, not live
`~/.codex` or private thread data. It should measure:

- append/upsert throughput;
- batch size and transaction shape;
- concurrent reader behavior during writes;
- cancellation and backpressure;
- index/update behavior for `provider_events`, `thread_turns`,
  `thread_items`, `message_receipts`, and `lane_runtime_state`.

The result should separate operation-shape problems from storage-engine
problems. Turso/libSQL should be reconsidered when measured SQLite contention
or sync/vector needs justify the extra integration cost.

### 3. Semantic Search

Semantic search should index derived artifacts, not raw logs by default.

Good initial index candidates:

- thread summaries;
- turn summaries;
- explicit decisions and errors;
- tool and file references;
- goal and retro summaries;
- message receipt summaries;
- operator notes.

Default exclusions:

- raw provider payloads;
- full transcripts;
- secrets and credentials;
- large tool outputs;
- debug captures;
- unreviewed private attachments.

Turso/libSQL vector search may be the right storage family later because it can
keep vector indexes near normalized SQL history. But embedding policy,
retention policy, and redaction rules come first.

### 4. Multi-Machine Sync

Multi-machine Dispatch should sync selected state, not replicate every local
database row.

Likely sync candidates:

- machine identity and presence;
- peer pairing records and capabilities;
- lane refs and routing metadata;
- compact thread/lane summaries;
- inbox and subscription state;
- message receipts and delivery acknowledgements;
- explicit operator-approved notes or artifacts.

Default non-sync data:

- raw provider events;
- full transcripts;
- tool outputs;
- debug payloads;
- local filesystem paths beyond route/workspace metadata;
- secrets and provider credentials.

Durable queues are mandatory. Live-only remote calls will fail whenever a
laptop sleeps, a network changes, or a peer is temporarily offline.

Turso Sync or libSQL replicas may be useful for selected-state sync. That should
be evaluated separately from whether the local registry backend should change.

### 5. Cloud Gateway Boundary

The gateway should route intent, not collect history.

Gateway-owned state:

- surface installations;
- routes and policy;
- machine registry and presence;
- pairing state;
- queue metadata and idempotency keys;
- external delivery receipts;
- audit entries for gateway actions;
- optional compact summaries explicitly approved for external rendering.

Local Dispatch-owned state:

- provider event logs;
- normalized thread history;
- raw payload retention and debug capture;
- semantic indexes over local history;
- detailed tool outputs and transcript data;
- local secrets, credentials, provider accounts, and filesystem authority.

The gateway may receive high-signal lifecycle updates so Slack or Linear can
show status. It should not receive broad agent logs unless a route/policy
explicitly opts into a compact artifact or export.

## Linear Plan

- `DIS-20` - parent: local substrate roadmap.
- `DIS-21` - storage connection/transaction boundary and dual-backend contract
  tests.
- `DIS-22` - concurrent event-ingestion load harness and operational metrics.
- `DIS-23` - semantic history search substrate and embedding retention policy.
- `DIS-24` - multi-machine Dispatch sync with selected state and dropout queues.
- `DIS-25` - Cloud Gateway boundary clarification.

## Execution Strategy

Work in milestones, with review after each:

1. Roadmap and gateway boundary
   - Land this note and clarify the gateway boundary.
   - Review for architectural contradictions and stale docs.

2. Storage boundary
   - Add the smallest testable connection/transaction boundary or document why
     the current store needs a smaller pre-step.
   - Keep SQLite/`aiosqlite` default.
   - Run selected compatibility checks against `pyturso`/`libsql` where cheap.

3. Event ingestion harness
   - Build an opt-in synthetic harness.
   - Record baseline results and revisit thresholds.

4. Semantic search substrate
   - Define artifacts, retention, and exclusion policy.
   - Add a fake-data prototype only if it does not require real embeddings or
     secrets.

5. Multi-machine sync design
   - Update ADR-0013/0014 or add a focused note that classifies selected state,
     queues, receipts, and conflict behavior.

Each milestone should run focused verification plus local review. A milestone is
not done while P0/P1/P2 findings remain open.

## Open Questions

- Is `pyturso` local embedded the right first optional backend, or should
  `libsql` remote/embedded replicas be the first production candidate?
- Should Dispatch use a dedicated storage writer thread for any synchronous
  alternate backend?
- Which semantic artifacts should be generated eagerly versus lazily?
- What selected-state sync is safe enough to enable by default between personal
  machines?
- What exact gateway update payload is enough for Slack/Linear without leaking
  history?
