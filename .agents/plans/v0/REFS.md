# dispatch v0 — references

Paths are repo-relative.

## Packet
- `.agents/plans/v0/PLAN.md` — phased plan (Graphite branch per phase, gates, verification).
- `.agents/plans/v0/GOAL.md` — pasteable `/goal` + loop contract.
- `.agents/plans/v0/RETRO.md` — durable execution ledger (resume here).
- `docs/development/design.md` — approved design spec.
- `.agents/plans/PLANNING.md` — repo planning + execution conventions.

## Decisions (`docs/adrs/`)
- 0000 contract-first/surface-derived · 0001 typed-exceptions-over-Result · 0002 single-daemon-over-one-app-server · 0003 own-scheduler · 0004 single-sourced agent docs.
- 0005 lane-authority capability ladder (**proposed**, gated on Phase-1 spike) · 0006 handler Ctx/DI · 0007 normalized LaneEvent vocabulary · 0008 control-socket protocol (JSON-RPC-lite/JSONL) · 0009 MCP daemon lifecycle (**proposed**).

## Verified research (in-repo)
- `docs/research/app-server-verification.md` — transports, turn/item grammar, approvals loop, resume fan-out, sandbox encodings, `result.data`, ephemeral default, schema regen (vs `codex-cli 0.136.0-alpha.2`).
- `docs/research/orchestration-thesis.md` — in-band vs out-of-band; messaging primitives; automations are filesystem/daemon, not protocol.

## Spikes (seed of the integration suite)
- `spikes/` — App Server probe scripts; see `spikes/README.md`.

## Open PRs (review-findings stack, draft)
- `#1` fix-review-converged → `main` (converged review fixes).
- `#2` docs-decision-adrs → `#1` (ADRs 0005-0009 + Phase-1 backpressure spike).

## Source-of-truth commands
- Schema: `codex app-server generate-json-schema [--experimental] --out <dir>` (pin the binary).
- Quality gate: `just check`.

## Trails inspiration (external, principles only)
- `~/Developer/outfitter/trails/docs/why-trails.md`, `docs/adr/0035-surface-apis-render-the-graph.md`, `docs/surfaces/mcp.md`.
