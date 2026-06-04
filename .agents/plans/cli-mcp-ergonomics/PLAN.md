# CLI/MCP Ergonomics — implementation plan

One-branch implementation packet for reshaping dispatch's operator surfaces
without breaking the contract-first/no-drift architecture. This is intentionally
a single big swing, not a Graphite stack. Goal loop: [`GOAL.md`](./GOAL.md).
Execution ledger: [`RETRO.md`](./RETRO.md). References: [`REFS.md`](./REFS.md).

## Objective

Ship a coherent CLI/MCP projection centered on:

- `dispatch send` and `dispatch stop`
- first-class `dispatch lane`
- grouped `dispatch goal`
- grouped `dispatch trigger`
- jq-friendly `--json` output and generated schemas
- MCP tools grouped by workflow/safety boundary, not CLI spelling

## Product grammar

```bash
dispatch send @docs "Review the README."
dispatch send @docs "Also check ADRs." --steer
dispatch send @docs "After this finishes, summarize risks." --queue
dispatch send @docs "Stop that and fix tests first." --interject
dispatch send @docs --context "Prior decision: use lane publicly, thread internally."

dispatch stop @docs
dispatch stop --lane @docs

dispatch lane get @docs
dispatch lane status @docs
dispatch lane list
dispatch lane list --unmanaged
dispatch lane attach <thread-id>
dispatch lane rename @docs docs-review
dispatch lane tail @docs
dispatch lane tail @docs --follow
dispatch lane fork @docs --name docs-copy
dispatch lane rollback @docs --turns 1
dispatch lane compact @docs
dispatch lane archive @docs

dispatch goal status @docs
dispatch goal set @docs "Loop until checks are green."
dispatch goal clear @docs

dispatch trigger add --lane @docs ...
dispatch trigger list
dispatch trigger rm <trigger-id>
dispatch trigger pause <trigger-id>
dispatch trigger resume <trigger-id>

dispatch daemon status
dispatch daemon log --limit 20

dispatch schema send
dispatch preset list
dispatch preset new reviewer
```

## Decisions

- `send` is the primary verb for putting instructions or context into a lane;
  it pairs with `stop`.
- `stop` is cancel-only. It maps to App Server `turn/interrupt` and sends no
  replacement message.
- `--steer`, `--queue`, `--interject`, and `--context` are first-class send
  flags. Equivalent `--mode steer|queue|interject|context` is automation-friendly.
  The choices are mutually exclusive.
- `--interject` means stop the active turn, then start the replacement message.
- `--context` means model-visible context injection only; it does not wake the
  lane.
- Keep `--lane` for operational commands. It is clearer than `--to`.
- `lane list --unmanaged` replaces a standalone `discover` concept.
- `open` folds into `new --no-send` or an equivalent explicit flag; `attach`
  becomes `lane attach`.
- `tail` should consolidate `transcript` and `watch` if it can do so honestly.
  Until true streaming exists, never describe bounded event samples as infinite
  tails.
- History/lifecycle controls move under `lane`.
- `up`/`down` can remain short; daemon status/logging should live under
  `daemon`.
- Config and presets get operator-facing inspection first: `preset list`, then
  a minimal `preset new <name>` only if the config writer is well understood.
- No backwards-compatible CLI aliases are required yet.

## Implementation guidance

- Preserve author-once derivation. Add projection metadata/grouping to the
  contract layer instead of hand-writing a separate CLI/MCP world.
- Internal ops and handlers may remain small and mechanical: send/start, steer,
  context injection, stop, lane read/list/attach/rename, goal lifecycle, trigger
  lifecycle, daemon read, schema, preset.
- MCP should stay workflow-shaped. A single lane-management MCP tool may expose
  `get/list/attach/rename/tail` actions; it should not mirror every CLI
  subcommand one-for-one.
- If the full surface is too much for one clean PR, prioritize:
  `send`, `stop`, `lane get/list/attach/rename`, grouped `goal`, grouped
  `trigger`, JSON/schema derivation, and MCP grouping. Defer broader reshapes
  only with tests proving current behavior and explicit follow-up notes/issues.
- Treat `--queue` as real only if durable behavior is implemented and tested.
  If not, fail clearly and document the unsupported mode.

## Verification

- CLI projection tests for `send`, `stop`, `lane get/list/attach/rename`,
  grouped `goal`, grouped `trigger`, `daemon status/log`, and schema output.
- Handler/contract tests for lane rename and any new composed send modes.
- MCP projection tests proving every op remains reachable through grouped tools
  with matching schemas, intent/idempotence annotations, examples, and error
  semantics.
- JSON/schema tests: representative commands support jq-friendly `--json`, and
  `dispatch schema <command>` returns a stable derived schema.
- Docs/skills updated to the new grammar.
- Smoke representative CLI help/commands with isolated dispatch/Codex state
  where needed; never touch live user agent state in tests.
- Final gate: `just check`.

## Done

Done only when the new grammar is implemented, docs/skills are updated, local
checks pass, local review has no unresolved P0/P1/P2 findings, and the branch is
ready for PR/submission. Record verification, review, deferred scope, and final
state in [`RETRO.md`](./RETRO.md).
