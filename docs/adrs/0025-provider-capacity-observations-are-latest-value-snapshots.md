---
id: 0025
slug: provider-capacity-observations-are-latest-value-snapshots
title: Provider Capacity Observations Are Latest-Value Snapshots
status: accepted
created: 2026-07-14
updated: 2026-07-14
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0025: Provider Capacity Observations Are Latest-Value Snapshots

## Context

Dispatch normalizes Codex and Claude account, runtime, capacity, and usage facts
into `ProviderCapacityObservation`. Provider probes refresh at different times,
and a future mesh heartbeat needs to carry the same provider-neutral shape
between nodes without exposing provider credentials or raw probe responses.

The observation table must remain useful when a probe is temporarily
unavailable. It must also avoid becoming a second metrics warehouse alongside
the bounded daily usage and provider-event history already owned elsewhere.

## Decision

Store one latest-value observation for each `(provider, host_scope,
config_scope)` tuple. A refresh replaces that tuple's row while observations for
other hosts and config scopes remain independent.

`host_scope` is the stable node selector (`local` today) and `config_scope`
distinguishes provider configurations on that node. The model is JSON
serializable and can be embedded in a future mesh heartbeat, but mesh transport
and node identity assignment are separate decisions.

Each observation carries an overall `observed_at` plus component timestamps for
account, runtime, capacity, and usage. Capacity windows carry their own
timestamps. Provenance is a bounded list of sources that contributed to the
observation. Consumers compute freshness and staleness from those timestamps and
an operator-supplied threshold at read time. Stale snapshots remain available;
there is no TTL deletion or freshness timestamp fabricated by a failed refresh.

Provider state uses the bounded vocabulary `ready`, `partial`, `signed_out`,
`disabled`, `unsupported`, and `unavailable`. A provider with no observation is
represented by absence plus an operator hint, not by a synthetic `missing` row.

The snapshot table is not a trend store. Bounded provider daily usage supplies
the history currently needed by usage surfaces. Future trend requirements must
use an explicit bounded history projection or the provider event log rather than
silently changing latest-value snapshot retention.

Persist only normalized identity fields:

- account email is a masked display label plus a one-way fingerprint;
- opaque organization identifiers are one-way fingerprints;
- a bounded organization display name may be retained as a label;
- tokens, cookies, auth files, OAuth material, keychain values, raw probe
  responses, and mutation handles are never stored in observations.

## Consequences

### Positive

- Local usage and future mesh heartbeat surfaces share one serializable model.
- Partial refreshes preserve useful older components without claiming they are
  fresh.
- Read-time staleness is deterministic and lets operators choose an appropriate
  threshold without rewriting stored data.
- Storage stays bounded to one snapshot per provider, host, and config scope.

### Tradeoffs

- Snapshot rows alone cannot answer arbitrary historical trend questions.
- Provenance is per observation rather than per scalar field; component
  timestamps provide the finer-grained freshness boundary.
- Missing-provider state must be inferred from absence and the requested scope.

## Alternatives considered

- **Append every observation** — duplicates the provider event/history substrate
  and creates retention work before a concrete trend query requires it.
- **Delete observations after a TTL** — loses useful last-known state and makes
  temporary provider failures look like providers that were never observed.
- **Store raw email and organization identifiers** — provides little operator
  value while increasing privacy risk in a local registry and future heartbeat.
- **Create synthetic missing-provider rows** — confuses discovery absence with a
  provider response and invents an observation timestamp without a source.

## References

- [ADR-0013: Dispatch Mesh Is Daemon Federation](0013-dispatch-mesh-is-daemon-federation.md)
- [ADR-0023: Provider Event Log and History Index](0023-provider-event-log-and-history-index.md)
