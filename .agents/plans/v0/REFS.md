# dispatch — references

Paths are repo-relative.

## This packet
- `docs/development/design.md` — approved design spec.
- `.agents/plans/v0/PLAN.md` — phased implementation plan.
- `docs/adrs/` — architecture decision records.

## Verified research (in-repo, durable)
- `docs/research/app-server-verification.md` — transports, turn/item grammar, approvals loop, multi-client resume fan-out, sandbox encodings, `thread/list` `result.data`, ephemeral default, schema regen. (Verified against `codex-cli 0.136.0-alpha.2`.)
- `docs/research/orchestration-thesis.md` — in-band vs out-of-band orchestration; messaging primitives (`turn/start`=send, `inject_items`=brief, `turn/steer`+`expectedTurnId`); automations are filesystem/daemon, not protocol.

## Spikes (seed of the integration suite)
- `spikes/` — App Server probe scripts; see `spikes/README.md` for what each proves.

## Trails inspiration (external, principles only)
- `~/Developer/outfitter/trails/docs/why-trails.md` — author/derive/override; drift-is-harder-than-alignment.
- `~/Developer/outfitter/trails/docs/adr/0035-surface-apis-render-the-graph.md` — `derive → create → surface`.
- `~/Developer/outfitter/trails/docs/surfaces/mcp.md` — trail→MCP-tool projection.
- `~/Developer/outfitter/trails/AGENTS.md` + `docs/adr/` — agent-doc + ADR structure mirrored here.

## Source-of-truth commands
- Schema: `codex app-server generate-json-schema [--experimental] --out <dir>` (pin the binary).
