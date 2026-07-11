# Goal Spec: App Server Adoption

Date: 2026-07-09
Status: Ready

## Objective

Land the clear, high-value Codex App Server 0.144 adoption work across
interactive request handling, canonical history ingestion, thread topology,
account usage, bounded resume, permission profiles, and rich inputs while
preserving Dispatch's contract-first architecture and provider-neutral database
substrate.

## Context

PRs #73 and #74 establish unmanaged-thread pickup and the current App Server
compatibility baseline. Linear's App Server adoption plan identifies the next
product work. The urgent correctness gap is that Dispatch only normalizes two
of the current server-request categories; an unhandled request can block a turn
without becoming actionable. The current canonical item, ancestry, usage, and
resume APIs also unlock substantial improvements to Dispatch's database-backed
oversight model.

## Scope

### In

- Reconcile and land the existing #73/#74 baseline before dependent work.
- `DIS-42`: complete, visible, configurable interactive request handling.
- `DIS-44`: canonical 0.144 item ingestion into provider-neutral history.
- `DIS-45`: Codex parent/descendant topology reconciled with Dispatch lanes.
- `DIS-35` and `DIS-39`: Codex capacity probes and first-class
  `dispatch usage` CLI/MCP/docs.
- `DIS-18`: metadata-only resume, bounded recent bootstrap, and incremental
  history continuation.
- `DIS-46`: permission profile discovery and preset integration.
- `DIS-47`: image inputs for authored `new` and `send` operations.
- Tests, fixtures, migrations, docs, CLI help/schema, MCP projections, and
  first-party skills required by each behavior slice.
- Linear status/comments and GitHub/Graphite PR state needed for an accurate
  execution record.

### Out

- Realtime voice implementation; the Linear document is the handoff for that
  future spike.
- The durable policy engine in `DIS-43`, beyond the small explicit policy needed
  by `DIS-42`.
- Reset-credit redemption, remote-control/mesh implementation, gateway/UI work,
  Slack/Claude runtimes, and production audio/media handling.
- PyPI publishing or a Dispatch release/version bump.
- Broad refactors unrelated to these issues.

## Source Of Truth

- `AGENTS.md` - repository guidance and contract-first rules.
- `docs/development/design.md` - approved architecture.
- `docs/adrs/0023-provider-event-log-and-history-index.md` - provider-neutral
  event/history contract.
- `tests/fixtures/app_server/protocol_manifest/current.json` - adopted protocol
  inventory.
- Linear `DIS-41` and its children - issue intent and acceptance criteria.
- Linear `DIS-34`, `DIS-35`, `DIS-39`, and `DIS-18` - existing usage and sync
  work retained rather than duplicated.

## Acceptance Criteria

- Every current stable server request is classified and cannot silently strand
  a turn; supported requests complete and unsupported requests become visible
  with explicit safe outcomes.
- Canonical 0.144 items are dispositioned and useful fields are queryable from
  the provider database without requiring raw JSON.
- Parent/descendant relationships survive rename/archive and remain distinct
  from Dispatch management authority.
- `dispatch usage` is a first-class jq-friendly CLI operation with equivalent
  grouped MCP behavior and redacted provider observations.
- Live observation can resume immediately and backfill recent-to-old history
  within explicit bounds and durable watermarks.
- Permission profiles compose predictably with presets and omitted Codex
  defaults.
- Text plus supported image inputs work through derived CLI/MCP surfaces.
- Every milestone has focused tests, current fixtures, docs/skills, a live
  isolated smoke where behavior requires it, and a 5/5 local review with no
  unresolved P0/P1/P2.
- Completed work is merged in coherent Graphite slices, Linear reflects the
  result, and the repository ends clean on current `main`.

## Decisions

- Use stacked, issue-shaped PRs rather than one oversized branch.
- Finish independent later milestones even if one slice has a documented hard
  blocker.
- Use a small explicit request-policy configuration now; defer the general
  policy language to `DIS-43`.
- Persist normalized transcript/item/topology/usage facts, not secrets or raw
  binary payloads.
- Keep experimental methods capability-gated and retain stable fallbacks.
- Do not add one MCP tool per App Server method.

## Risks

- Some interactive request categories require host-specific result shapes or
  credentials Dispatch must not synthesize.
- Current canonical items may differ between live notifications and persisted
  thread history.
- Experimental turn/item pagination may drift; production behavior must retain
  a stable fallback.
- The existing #73/#74 stack must merge cleanly before dependent branches can
  be based on `main`.
- Live scenarios may consume model capacity; use a current inexpensive model
  and low reasoning with small synthetic prompts.
