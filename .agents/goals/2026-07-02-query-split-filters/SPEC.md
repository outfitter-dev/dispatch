# Goal Spec: query-split-filters

Date: 2026-07-02
Status: Ready

## Objective

Split App Server-backed `dispatch search` from Dispatch-local indexed `dispatch query`, then make `query` the structured local substrate surface for tool calls, file refs, thread refs, and other indexed filters.

## Context

`dispatch search --local` overloads one command with two contracts. App Server search is broad, Codex-owned, useful for unmanaged or not-yet-synced threads, and comparatively opaque. Local indexed search is Dispatch-owned, fast, DB-backed, filterable, and should become a first-class operator and MCP surface.

Live checks on 2026-07-02 showed local indexed search returning in roughly 0.3s after daemon restart, while App Server search took roughly 1.3-2.9s for similar examples and returned different coverage. Live history inspection also showed raw MCP tool-call payloads with concrete fields such as `server`, `tool`, `status`, `arguments`, `error`, and `durationMs`, but local text search did not find concrete tool names such as `linear.save_issue`.

## Scope

### In

- `DIS-29`: make `dispatch query` a first-class local indexed query op and keep `dispatch search` App Server-backed.
- `DIS-30`: add structured filters backed by `thread_items`, `thread_item_refs`, lane metadata, and sync/history facts.
- `DIS-31`: promote safe concrete tool-call metadata into queryable indexed fields where needed.
- `DIS-32`: share query/history filter semantics enough to avoid drift.
- `DIS-33`: update docs, skills, CLI help/schema, and MCP guidance for the new grammar.
- Tests, local review loops, PRs, merge, and tracker updates.

### Out

- Semantic/vector search.
- Turso/libSQL backend migration.
- Multi-machine sync.
- Remote/cloud query service.
- Large raw result-body indexing by default.
- Paid APIs or external credentials.

## Source Of Truth

- `DIS-20` - local substrate umbrella.
- `DIS-29` - command/product split for search vs query.
- `DIS-30` - structured indexed filters.
- `DIS-31` - concrete MCP tool-call metadata.
- `DIS-32` - shared query/history filter semantics.
- `DIS-33` - docs and skills update.
- `DIS-28` - adjacent daemon/client version-skew guardrail.
- `AGENTS.md` - project rules and contract-first surface derivation.
- `docs/development/design.md` - architecture and op/surface philosophy.
- `docs/adrs/0018-top-level-thread-actions-and-search.md` - current search framing.
- `docs/adrs/0023-provider-event-log-and-history-index.md` - normalized history/event substrate.
- `src/outfitter/dispatch/core/models.py` - search/history input/output contracts.
- `src/outfitter/dispatch/core/handlers.py` - current search/history handlers.
- `src/outfitter/dispatch/registry/store.py` - `thread_items` and `thread_item_refs` backing data.

## Acceptance Criteria

- `dispatch search` is App Server-backed only and no longer advertises or implements `--local`.
- `dispatch query` is a separate op with its own schema, CLI help, MCP projection, tests, and docs.
- `dispatch query [query]` searches the local managed-history index.
- `dispatch query` accepts an omitted text query only when at least one structural filter is present.
- Query results expose jq-friendly item-level fields: lane/thread identity, item id, turn id, type, role, tool, snippet, file refs, thread refs, and relevant timestamps.
- Filters use indexed tables/refs, not post-processed CLI output.
- Concrete MCP tool calls such as `linear.save_issue` are discoverable without requiring `history --raw`.
- `history` and `query` share matching semantics where they overlap, or intentional differences are documented and tested.
- MCP guidance distinguishes broad App Server search from local indexed query.
- Docs and first-party skills no longer teach `search --local` as the canonical local path.
- Local reviews find no unresolved P0/P1/P2 issues before merge.

## Decisions

- `query` is not an alias for `search --local`; it is a separate local substrate contract.
- `search` remains the broad App Server surface.
- `history` remains the inspection surface for known threads.
- `sync` remains the operation that populates or refreshes the local index.
- Prefer safe normalized metadata over raw payload indexing; raw retained payloads can support inspection but should not become the default search substrate for sensitive or huge result bodies.

## Risks

- Filter sprawl could make the CLI noisy; keep the first slice focused on data already present and useful.
- Query/history sharing could become over-abstracted; extract only the matching semantics needed to avoid drift.
- Tool-call metadata can contain sensitive values; index names/status/duration/error presence before argument/result contents.
- Removing `--local` can break recent muscle memory, but no public compatibility burden exists yet.
