# Stable refs and flat CLI - implementation plan

Follow-up packet for making dispatch easier to operate with durable short refs, a flatter CLI, and MCP tools that stay grouped for agents. Pasteable goal: [`GOAL.md`](./GOAL.md). Execution ledger: [`RETRO.md`](./RETRO.md). References: [`REFS.md`](./REFS.md).

This packet assumes the top-level thread action work from PR #32 is available, or the implementation branch is stacked on it. If PR #32 has not merged, start from that branch; otherwise start from current `main`.

## Objective

Ship a cleaner public model:

- Codex UUIDv7 remains the canonical durable thread id and is always accepted.
- Dispatch assigns every managed lane a short local `ref` for daily CLI/MCP use.
- Titles and `@handles` are mutable convenience labels, not stable identity.
- Common thread operations move to top-level CLI commands instead of `dispatch lane ...`.
- `tail` and `watch` split into persisted history vs bounded live event sample.
- `dispatch mcp` remains the top-level MCP server entrypoint.
- MCP remains grouped by workflow and safety boundary, not mirrored one-command-per-CLI-route.

## Product model

Use these terms consistently:

- **Codex thread id**: full UUIDv7 from Codex/App Server. Canonical global identity. Always accepted.
- **Dispatch ref**: short registry-assigned id for a managed lane. Stable within the dispatch registry.
- **Title/name**: mutable Codex thread title. Agents may update it as work progresses.
- **Handle/alias**: optional human-friendly convenience selector, often `@...`, never the primary stable id.
- **Managed**: registered in dispatch.
- **Unmanaged**: visible Codex thread not registered in dispatch.
- **Synced**: dispatch refreshed its local index/cache for a managed lane. Sync does not grant ownership or write authority.

Internal code may keep using `lane` where it means "managed thread with registry state." User-facing CLI/help/docs should prefer "thread," "ref," "title," and "managed/unmanaged" unless the internal distinction is being explained.

## Dispatch ref format

Use an 8-character maximum ref with a 6-character initial format:

```text
<source><payload4><mixer>
```

For Codex:

```text
0k7M4a
```

Rules:

- `source`: one source/harness character. Use `"0"` for Codex.
- `payload4`: first four base58btc characters derived from `sha256("codex:" + thread_id)`.
- `mixer`: one collision-allocation character chosen by the registry.
- Base58btc alphabet: `123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz`.
- First attempt uses the first mixer character in the alphabet. If the ref is already allocated to a different thread, try the next mixer character until a free ref is found.
- Store the allocated ref. Do not recompute a different ref on every display.
- If the mixer alphabet is exhausted for one payload, fail loudly and require the full Codex thread id. This should be practically unreachable for a local registry.

The ref is dispatch-local. If a registry is deleted and rebuilt, refs will usually be identical, but a collided mixer can differ depending on allocation order. That is acceptable because the full Codex UUID is the durable escape hatch.

## Selector resolution

Add one shared selector resolver used by core handlers and all surfaces. It should return a structured resolved target with thread id, managed lane when present, selector kind, ref when known, title/name, source, and ambiguity candidates when resolution fails.

Resolution order:

1. Exact dispatch ref.
2. Exact full Codex thread id.
3. Exact managed lane id, if distinct.
4. Exact managed handle/alias, if unique.
5. Exact current title/name, if unique.
6. Historical title/name/alias, only if implemented in this slice and unique.
7. Fuzzy title matching only for read/discovery flows, not for mutating or destructive ops.

Mutating/destructive ops must not guess. If a selector is ambiguous, return a typed validation/not-found style error with candidate rows containing `ref`, full id prefix, title/name, managed state, and cwd when available.

## CLI surface

Promote the common thread operations to top level:

```bash
dispatch new ...
dispatch attach <thread-id>
dispatch list
dispatch list --unmanaged
dispatch get <selector>
dispatch send <selector> <text>
dispatch stop <selector>
dispatch tail <selector>
dispatch watch <selector>
dispatch sync <selector>
dispatch rename <selector> <title>
dispatch archive <selector>
dispatch restore <selector>
dispatch search <query>

dispatch goal status <selector>
dispatch goal set <selector> <objective>
dispatch goal clear <selector>

dispatch trigger add|list|rm|pause|resume
dispatch daemon status|log
dispatch schema <command>
dispatch mcp
dispatch doctor
dispatch up
dispatch down
```

Reserved top-level commands are `new`, `attach`, `list`, `get`, `send`, `stop`, `tail`, `watch`, `sync`, `rename`, `archive`, `restore`, `search`, `goal`, `trigger`, `daemon`, `schema`, `mcp`, `doctor`, `up`, and `down`. Do not let generic selector parsing intercept reserved words. `dispatch mcp` must remain the MCP server entrypoint.

Specific CLI semantics to preserve:

- `dispatch new` is the creation path for a new dispatch-owned Codex thread. Opening a thread without sending an initial payload is `dispatch new --no-send`; do not keep a separate canonical `open` command for that.
- `dispatch attach <thread-id>` registers an existing Codex thread as managed by dispatch. It must not start a turn.
- `dispatch sync <selector>` refreshes dispatch's local index/cache for a managed thread. Sync is separate from ownership and does not turn unmanaged threads into writable owned threads.
- `dispatch list` should be the ordinary overview: show managed threads with `ref`, title/name, full id or id prefix, status, cwd/repo when available, sync state, and useful recency/last-event facts when cheap.
- `dispatch list --unmanaged` is the discover path for visible Codex threads that dispatch does not manage yet. Do not keep a separate canonical `discover` command.
- `dispatch get <selector>` is the focused single-thread status/metadata view.
- `dispatch search <query>` should support global search plus useful filters: managed/unmanaged, `--thread <selector>` for one focused thread, `--repo <path>` or `--dir <path>` when App Server/dispatch metadata can support it, and `--since`/`--until` date windows.
- `dispatch rename`, `archive`, and `restore` should accept managed refs and full Codex thread ids. `restore` only unarchives; it must not resume or start a turn.
- `dispatch stop <selector>` should accept the selector positionally. `--lane` is not the canonical shape.

Remove `tail --follow` from the primary surface. Use:

- `dispatch tail <selector>` for persisted conversation history from `thread/read(includeTurns:true)`.
- `dispatch watch <selector>` for a bounded live App Server event sample with `--limit` and `--timeout`.

Do not claim infinite streaming until the control socket can support a real subscription. A future true stream can become `dispatch watch --follow`; do not reserve that behavior for `tail`.

No backwards-compatible `lane` aliases are required yet unless they materially simplify incremental implementation. If aliases are temporarily kept during transition, docs/help/schema parity should still present the flat commands as canonical.

## MCP surface

Keep MCP grouped by workflow and safety boundary. Do not mirror every flattened CLI command as a separate tool.

Rename or reshape lane-named MCP tools toward thread/ref language while preserving intent grouping:

- `dispatch_thread_read`: get/list/discover/tail/watch/search-style read actions.
- `dispatch_thread_write`: new/attach/sync/send/stop/rename/restore/fork/compact-style write actions where intent is non-destructive.
- `dispatch_thread_destroy`: archive/rollback and other destructive actions.
- `dispatch_goal`: goal status/set/clear, grouped only if annotations remain correct; otherwise split read/write/destroy as the current projection requires.
- `dispatch_trigger_read`, `dispatch_trigger_write`, `dispatch_trigger_destroy` or the existing equivalent if annotations stay exact.
- `dispatch_daemon_read`: daemon status/log.

Do not mix different MCP safety annotations inside one tool if the current projection cannot represent that accurately. If grouping by workflow would blur `readOnlyHint` or `destructiveHint`, split by intent.

Every MCP structured output that identifies a managed thread should include `ref`, full id, title/name when available, managed state, source, status, and cwd when available. Inputs should accept the same selector strings as CLI inputs.

## Registry and models

Add ref storage to managed lanes:

- `ref` string, unique and non-null for every managed lane after migration.
- Optional allocation metadata if useful for tests/debugging: `ref_source`, `ref_payload`, `ref_mixer`.
- Title/name snapshot as a separate concept from handle/alias if not already modeled cleanly.

Add a migration that backfills refs for existing lanes. Allocate in stable order by created timestamp, then lane id, so rebuild behavior is deterministic when possible.

Outputs that currently return `LaneRef`, lane list items, search matches, discovered sessions, and thread action refs should expose `ref` when known. Unmanaged discover/search results may omit `ref` unless they have been indexed into a local cache; do not invent refs for unmanaged threads unless they are stored and resolvable.

## Docs, skills, and ADRs

Update durable docs and agent-facing skills as part of the implementation, not as follow-up cleanup:

- README quick start and examples use flat commands and refs.
- `docs/usage/README.md` explains refs, full Codex IDs, mutable titles, managed/unmanaged/synced, `tail` vs `watch`, and `dispatch mcp`.
- `docs/development/design.md` reflects the public CLI, MCP grouping, and internal lane terminology.
- Add a new ADR for dispatch-local refs and flat thread CLI, or extend ADR-0018 if the team decides one ADR is enough.
- Update root `AGENTS.md`, `CLAUDE.md` shims, and path-scoped `.claude/rules/` when their lexicon or examples become stale.
- Update `.claude/rules/contracts.md` and client rules if route/schema examples change.
- Update `skills/dispatch/SKILL.md` to teach agents to prefer refs over handles and to use flat commands.
- Update `skills/dm/SKILL.md` so short-message workflows target refs/owned managed lanes and do not treat mutable handles as identity.
- Update plugin/MCP setup docs only if tool names or schemas change; keep `dispatch mcp` as the entrypoint.

## Verification

Required tests:

- Ref allocation for owned lanes, attached lanes, and forked lanes.
- Collision allocation path with a forced payload collision.
- Migration backfills unique refs.
- Resolver accepts ref, full Codex id, managed lane id, and unique handle/title.
- Resolver rejects ambiguous selectors with candidate data.
- Mutating/destructive ops do not fuzzy-resolve.
- Flat CLI routes invoke canonical ops with the expected params.
- `dispatch schema <flat command>` resolves to the canonical op for list/get/tail/watch/sync/attach/search/rename/archive/restore.
- `tail` and `watch` are separate CLI routes; `tail --follow` is not canonical.
- MCP projection tests reflect renamed/grouped thread tools and exact safety annotations.
- Output schemas include `ref` wherever managed threads are returned.
- Docs/skill drift checks by grep or targeted review: no canonical examples use `dispatch lane ...` unless explicitly explaining legacy/internal terminology.

Manual/local proving:

- Run `dispatch list --json`, `dispatch schema list`, `dispatch schema tail`, `dispatch schema watch`, and representative help output in-tree.
- With isolated `DISPATCH_HOME`, create or fake multiple lanes and verify refs remain stable across daemon restart.
- Optional live App Server smoke should be read-only/cheap, with isolated dispatch state and no sends to unrelated user sessions.

Final gate: `just check`, local review loop, and draft PR submission.

## Execution model

Prefer one branch, `feat/stable-refs-flat-cli`, unless the implementation becomes too large. If splitting becomes necessary, use this order:

1. Ref allocator, registry migration, output models.
2. Selector resolver and handler adoption.
3. Flat CLI routes plus `tail`/`watch` split.
4. MCP tool rename/grouping and schema parity.
5. Docs, skills, ADR, and final review cleanup.

Each branch must pass `just check` and a local review with no unresolved P0/P1/P2 before moving on. Keep PRs draft until CI and local review are clean.

## Done

Done only when every managed lane has a stable local ref, all relevant selectors accept refs and full Codex ids, the canonical CLI is flat, `tail` and `watch` are separate and honest, MCP remains grouped and annotation-correct, docs/skills/ADRs are updated, and local review plus `just check` are green.
