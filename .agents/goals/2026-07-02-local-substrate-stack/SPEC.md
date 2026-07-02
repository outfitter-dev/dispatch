# Goal Spec: local-substrate-stack

Date: 2026-07-02
Status: Active

## Objective

Turn the local substrate direction into landed Dispatch work: document the stack, clarify Linear/Gateway/local boundaries, then loop through as many implementation/design milestones as can be safely completed with review gates.

## Context

Matt's product pressure is concrete: multi-machine Dispatch, semantic search, and concurrent event ingestion would make Dispatch materially more useful. The Cloud Gateway remains useful for Slack/Linear ingress and routing, but it should not become the default agent-log sink.

The prior Turso/libSQL spike kept SQLite/`aiosqlite` as the default while proving the storage path can become more portable. This goal should now move from "Turso maybe later" to "build the local substrate shape that makes Turso/libSQL useful when the product earns it."

## Scope

### In

- Repo roadmap note for the local substrate stack.
- Linear parent/child issues for the stack.
- Cloud Gateway doc clarification: route intent, do not collect logs by default.
- Storage boundary exploration and small code/tests if safe.
- Event-ingestion harness if safe.
- Semantic search policy/design and fake-data prototype if safe.
- Multi-machine selected-state sync design.
- Review loops after each milestone.

### Out

- Making Turso/libSQL the default backend.
- Moving real user registry data or real `~/.codex` data.
- Using real cloud credentials, paid embedding calls, or real transcript embeddings.
- Implementing a Cloudflare gateway runtime.
- Publishing or releasing.

## Source Of Truth

- `docs/development/local-substrate-roadmap.md` - roadmap note for this stack.
- `docs/development/cloud-gateway.md` - gateway boundary.
- `docs/adrs/0013-dispatch-mesh-is-daemon-federation.md` - daemon federation.
- `docs/adrs/0014-mesh-auth-discovery-and-durable-queues.md` - durable queues.
- `docs/adrs/0023-provider-event-log-and-history-index.md` - event/history substrate.
- `docs/research/turso-libsql-storage-spike.md` - Turso/libSQL evidence.
- Linear `DIS-20` through `DIS-25`.

## Acceptance Criteria

- Linear `DIS-20` through `DIS-25` exist and are updated as work lands.
- Roadmap and gateway boundary docs are merged.
- Each attempted milestone has focused verification and local review.
- P0/P1/P2 findings are fixed before moving to the next milestone.
- Any deferred milestone records why it was deferred and what issue tracks it.
- Final PR stack is merged and local `main` is clean/synced, or a stop rule records the blocker.

## Decisions

- Cloud Gateway routes intent and may hold route/audit/delivery metadata; it is not the default store for agent logs/history.
- SQLite/`aiosqlite` remains the default store during this goal.
- Turso/libSQL work must proceed through small compatibility boundaries and synthetic probes.
- Semantic search must start from derived artifacts and retention policy, not raw transcript embeddings.
- Multi-machine sync should replicate selected compact state, not full local databases by default.

## Risks

- The stack can sprawl. Keep milestones independently reviewable.
- Storage boundary work can become a framework if not held tightly to call sites.
- Event benchmarks can overfit tiny synthetic workloads.
- Semantic search can create privacy risk if raw payload boundaries are vague.
- Multi-machine sync can accidentally duplicate Cloud Gateway responsibilities.
