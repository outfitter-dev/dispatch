---
id: 0028
slug: stations-own-provider-bindings-and-durable-execution
title: Stations Own Provider Bindings and Durable Execution
status: proposed
created: 2026-09-11
updated: 2026-09-12
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0028: Stations Own Provider Bindings and Durable Execution

## Context

Dispatch is adding Hermes alongside the existing Codex runtime and planned Claude provider. Network coordination also needs stable destinations, local authorization, durable delivery and truthful discovery. These efforts must share a foundation rather than each inventing provider identity, retries or completion semantics.

At the source baseline `8ad072c`, `Lane.id` also serves as a Codex thread ID, the daemon starts one Codex connection before serving, and handlers consume a Codex-specific client. Normalized history and receipt identities are provider-qualified but lack a configured runtime/profile namespace. The 0.12 delivery ledger already reserves keyed requests and preserves ambiguity. A lane-only identity field or a new provider enum would not make those paths safe for another runtime.

This proposal records the implementation direction for [DIS-78](https://linear.app/outfitter/issue/DIS-78). It does not claim provider-neutral execution, Hermes integration or network transport is implemented. [The execution plan](../../.agents/plans/stations-providers/PLAN.md) separates code gates from design decisions and preserves existing Claude ownership.

## Decision

### One local authority, native provider runtimes

A **station** is one Dispatch runtime boundary owning a local registry, policy and provider bindings. A host may contain isolated stations. Network membership and station incarnation are separate from the host name and from a provider connection generation.

Dispatch owns canonical ops, target resolution, local authorization, reservations, ordering, recovery policy and state projections. Codex, Hermes and Claude own native inference, tools, memory and canonical conversation history. Fixed typed adapters translate prepared requests and native evidence beneath the existing authored ops. They do not receive the entire registry, scheduler or surface context.

Keep one daemon per station. Provider process ownership, client connection ownership and human UI attachment are separate. Closing a client attached to an external Hermes service must not stop that service. The initial coding adapter instead owns a long-lived native stdio gateway child; a transient CLI/MCP client does not own that child, and loss of the daemon/pipe does not provide runtime survival. The Hermes runtime and the proposed Dispatch Network gateway are different services with different authority and receipts.

The [native Hermes investigation](../research/hermes-native-provider-contract.md) selects owned stdio because it supports per-session cwd. Plain text and dedicated Dispatch-owned sessions are the initial contract. Native acknowledgments lack durable keyed admission; local reservations prevent automatic duplicate submission and preserve unknown outcomes. Ordinary cold resume can auto-continue a stale crash marker even after prior terminal evidence, so automatic continuation across a gateway generation change requires a supported suppression guarantee. Until then, quarantine old sessions while permitting new independent work after fencing the old child. Desktop attachment and unrestricted autonomous-turn attribution retain separate proof gates. The [HTTP Runs alternative](../research/hermes-http-runs-contract.md) does not provide a fallback.

The source-pinned stdio audit adds a prerequisite to any durable Hermes receipt claim: on stock `939e45c91d751fadd94dcd1b873ac3cb44846213`, idle check and turn claim can race an unsolicited heartbeat or bot turn, while public terminal events carry no request/turn identity. Sequence fencing, a streaming acknowledgment and `running:false` cannot repair that ambiguity. [DIS-94](https://linear.app/outfitter/issue/DIS-94) is actively preparing an additive `prompt_turn_correlation_v1` contract with atomic admission, an owner token, and a native turn id echoed through start, terminal, error and retained evidence. Until its isolated upstream implementation is verified and adopted, Dispatch refuses durable Hermes execution rather than guessing a receipt result; generation quarantine remains an independent boundary.

### Stable thread identity, scoped native identity

| Identity | Meaning |
| --- | --- |
| Station ID and incarnation | Owning runtime and replacement/restore fence for future network targets. |
| Dispatch thread/lane key | Stable station-local managed identity; existing Codex lane keys and refs remain unchanged. |
| Runtime binding ID | Stable local configured provider namespace, independent of endpoint addresses and raw credentials. |
| Native session ID | Provider session identity inside a binding; continuation requires positive provider evidence. |
| Network operation ID | Immutable network request with authenticated origin and pinned destination; may exist before local admission. |
| Local delivery ID | Station reservation and receipt associated with a request. |
| Native run/turn ID | Evidence for a particular provider admission or execution. |
| Connection generation | Fence for client readiness and observations, not a new logical thread. |

A future managed network target is `(network_id, station_id, station_incarnation, thread_id)`. Its `thread_id` denotes the stable Dispatch key, not an unscoped native session ID. The station resolves binding and native identity internally. Provider continuation does not rename the public target; replacing a profile or installation does not silently retarget it.

Observed-only sessions require stable binding-scoped observation identity without automatically enrolling a writable lane. Preserve `@project:name` semantics. Station selection is a separate future contract; network scope must not silently change existing scripting output.

[DIS-79](https://linear.app/outfitter/issue/DIS-79) owns the additive migration. Preserve all existing Codex primary keys, refs and child foreign keys, and backfill transactionally. Audit every provider-qualified uniqueness constraint, query and join: threads, events, turns, items, refs, receipts, runtime state and topology. Two profiles with identical native IDs must never share evidence. Use the existing WAL-safe migration backup path. Do not enable Hermes writes with a partial backfill.

#### Public identity and selector compatibility

This proposal extends [ADR-0019](0019-dispatch-local-refs-and-flat-thread-cli.md) to additional providers while preserving its full Codex ID escape hatch:

- Existing and newly created threads in the default Codex binding retain their full native Codex ID as `lanes.id`. Existing refs and child foreign keys remain unchanged. Other providers allocate opaque Dispatch keys in a namespace disjoint from Codex IDs; they never derive refs with Codex-specific hashing.
- In local managed-thread outputs, `id` remains the stable Dispatch key and the existing `lane` compatibility field, wherever present, remains its alias. `ref` and `handle` retain their current meanings. Add `provider`, `binding_id`, and `provider_session_id` as local identity metadata; do not introduce another `lane_key` output alias or rename existing fields. For other bindings with opaque Dispatch keys, native session continuation may change `provider_session_id` with positive evidence while `id`, `lane`, and `ref` stay stable. The default Codex binding preserves `provider_session_id == id`; reassignment is unsupported.
- Existing managed ref, exact Dispatch key, handle and title resolution keeps its precedence. The full native Codex ID remains accepted, including unmanaged read paths, against the designated default Codex binding. Enabling Hermes does not redirect that fallback or make it ambiguous. Unsupported operations still fail at the authority/capability boundary.
- The first additional-provider slice selects managed threads by existing refs, Dispatch keys or labels. It does not accept a bare Hermes/Claude native ID or invent colon-qualified selector syntax. Native lookup within another binding requires a future explicit binding-scoped authored contract. Additional Codex execution bindings remain disabled until that contract is defined; storage collision tests cover them without implying public execution support.
- A network target's `thread_id` is the managed Dispatch key. Binding IDs and native IDs remain local metadata by default and are not remote authority tokens. Endpoint addresses, credentials and filesystem paths are never encoded into these identifiers.

DIS-79 must test old and newly created default-Codex output/selector behavior, additive non-Codex outputs, unchanged refs and foreign keys, duplicate native IDs across bindings, and rejection of unsupported native lookups. Do not weaken compatibility by declaring every unqualified native ID invalid when another provider is enabled.

### One local reservation path

Every local ingress converges on the existing reservation and scheduling authority. Preserve compatible unkeyed behavior; an operation ID does not make every handler idempotent.

For a supported keyed operation:

1. Validate input and immutable support constraints. Resolve its exact target and permitted settings.
2. Persist the reservation before any possible provider mutation, including first-session creation.
3. Claim submission in per-thread reservation order without holding a database transaction across provider I/O.
4. Recheck mutable authority, expiry and readiness immediately before submission.
5. Persist acknowledgment or uncertainty and reduce later evidence against that same receipt.

Exact retry reuses the persisted target, request and effective settings. Changed input conflicts. Handle renames, default changes, endpoint labels and transport routes cannot redirect admitted work. A possibly submitted request is reconciled; missing history, a missing mapping, changed credentials or expired provider replay never authorize a new automatic call. Ambiguous work holds later submissions on that thread while other threads may continue.

Dispatch orders reservations it has seen. A native provider queue owns execution order after acceptance; Dispatch must not start an extra turn to compensate. Preserve legacy/native queue ordering. There is no network-global FIFO or exactly-once execution promise.

### Trusted context and two immutable request records

Caller identity comes from trusted ingress outside model-editable op arguments. Local callers retain their current authority model. Remote callers need authenticated principal/delegation evidence; an adapter must not invent that identity or receive raw OAuth credentials.

The future gateway freezes network/principal/caller-key scope, target station/incarnation/thread, canonical submitted arguments, contract version, requested settings and work expiry. Settings known only to the station cannot be included as if they were known at gateway admission.

At its first reservation, the station freezes permitted effective settings and links their digest to the submitted request. Later retries do not recompute defaults or extend work expiry. Current delivery keys are registry-global; a receiver must derive a bounded key from authenticated remote scope and atomically map the network operation to its local receipt. Runtime `binding_id` and immutable request binding are distinct concepts.

Gateway admission, station reservation, provider acceptance and observed execution are separate facts. Transport leases and mailbox retries grant no provider writer authority. Direct and gateway paths retain the same operation identity and converge on that local mapping. No automatic route fallback is introduced in the first network slice.

### Capabilities and evidence have explicit limits

Keep provider support, caller authority, current availability and durable assurance distinct. An owned lane does not automatically support Codex goals, steering, rollback, history or compaction. Guard actual routes and advertised capabilities consistently; unsupported operations fail before workspace/provider mutation without fallback to Codex.

Adapters parse native evidence. The shared core defines acceptance, processing, completion, failure, interruption and uncertainty once. Live events, polling and restart recovery use the same transition path. Correlate binding/session/run/receipt, generation, source, provider time, receipt time and partiality. Wall-clock time alone does not order provider facts.

An old completion cannot overwrite a newer busy state. A stale connection cannot claim current readiness, but a positively correlated late result may resolve its original receipt. A terminal turn, readiness for another input and completion of all background work are different facts.

Expose bounded local observations: stable thread identity, provider kind, owned/attached/observed status, activity, supported actions, readiness reason, source, age and uncertainty. A future directory adds observer/path reachability, station epoch/sequence, receipt time, partiality and tombstones. Online station connectivity is not provider readiness; stale working observations are last-reported activity.

Keep raw history, provider events, absolute paths, credentials and full tool output local by default. Published titles and summaries also need an explicit policy. No second canonical conversation store or unlimited event ingestion is introduced.

### Independent progress and compatibility

The Hermes native API probe can proceed while the shared contract is reviewed. Its findings constrain supported adapter semantics. For this implementation Matt authorized dedicated synthetic conversations on the local default profile; automated tests and destructive fault simulations remain isolated. Local Hermes integration depends on the complete identity, routing, reservation, evidence and compatibility gates, not on building a cloud gateway or finishing Claude's UI transport.

[DIS-70](https://linear.app/outfitter/issue/DIS-70) is the first bounded code slice: validate the expected op schema at the receiving execution boundary. A separate preflight connection cannot protect against daemon replacement. An old receiver must reject a checked request it does not understand instead of silently ignoring a new field. Intentional legacy read compatibility needs separate proof; provider-bearing operations never fall back to unchecked execution.

Provider-independent startup is a separate [DIS-83](https://linear.app/outfitter/issue/DIS-83) slice. The first Hermes path may explicitly retain Codex as a temporary startup dependency. Remove that constraint before advertising Hermes-only stations or cached local discovery during Codex startup failures. One provider failure must not cancel sibling workers; shared registry failure remains a common failure boundary.

### Network decisions remain bounded

The product direction is private HTTPS over Tailscale, authenticated cloud MCP, a durable mailbox, network-wide authorized discovery and final local authorization. SSH is not the application transport. Network operations target stations rather than exposing raw provider APIs across machines.

The first cloud proof uses real authentication and a tiny manifest derived from canonical ops against synthetic inventory. Initial remote writes are queued plain text to existing threads. Creation, stop/steer, approval responses and automatic route fallback need separate contracts. A local provider capability does not automatically enter the remote allowlist.

Cloudflare Worker plus a SQLite-backed Durable Object is a candidate to validate. Gateway trust, actual client/auth compatibility, delegated credential lifetime, retention and rollout remain explicit decisions before remote writes. No claim of end-to-end confidentiality from a gateway that processes plaintext requests is made. SQLite remains the local default; database replication does not solve admission or provider ambiguity.

## Consequences

### Positive

- Hermes, Claude and network work consume the same identity, reservation and evidence foundation.
- Existing Codex references and ownership contracts remain compatible.
- Local progress does not depend on cloud infrastructure or another provider's UI transport.
- Receipt and readiness claims remain bounded by provider evidence.

### Tradeoffs

- Identity migration is substantive and must cover normalized storage and joins before enabling another provider.
- More precise capability and evidence types require focused tests and adapter-specific interpretation.
- Independent provider startup is deferred from the initial slice and must remain a visible limitation until proven.

## Alternatives considered

- **Add only a provider enum and lane field** — leaves Codex routing and cross-profile identity collisions unresolved.
- **Make each provider a station** — confuses local provider namespaces with registry/policy/network ownership.
- **Build the cloud gateway first** — does not establish safe local provider execution and delays the smallest useful proof.
- **Give every adapter its own queue and completion model** — duplicates recovery authority and permits incompatible receipt promises.
- **Replace SQLite or add a dynamic plugin SDK now** — neither is needed by the concrete Hermes path.

## References

- [Project and issue dependencies](https://linear.app/outfitter/project/dispatch-stations-and-providers-27755da2589a)
- [Architecture implementation contract](https://linear.app/outfitter/document/station-and-provider-architecture-implementation-contract-6ecb3d6f4aed)
- [Portable Hermes handoff](https://linear.app/outfitter/document/hermes-agent-handoff-prerequisites-workflow-and-pickup-gates-5dd9c614285a)
- [Delivery contract](../usage/deliveries.md)
- [ADR-0013](0013-dispatch-mesh-is-daemon-federation.md), [ADR-0014](0014-mesh-auth-discovery-and-durable-queues.md), [ADR-0023](0023-provider-event-log-and-history-index.md), [ADR-0024](0024-provider-thread-topology-is-independent-of-lane-authority.md), [ADR-0026](0026-claude-control-uses-resume-processes-and-hooks.md)
