# Goal References: query-split-filters

Use this as the evidence index for the goal. Prefer short notes with links or paths over long copied excerpts.

## Repo Guidance

- `AGENTS.md` - project guidance, contract-first rule, commands, and lexicon.
- `.claude/rules/contracts.md` - author-once derive-surfaces discipline.
- `.claude/rules/client.md` - App Server access boundary.
- `.claude/rules/python-conventions.md` - async/core and Python style.

## Tracker

- `DIS-20` - local substrate roadmap parent.
- `DIS-28` - daemon/client version-skew issue, adjacent guardrail.
- `DIS-29` - split App Server search from local indexed query.
- `DIS-30` - add structured filters to local query.
- `DIS-31` - promote concrete MCP tool-call metadata.
- `DIS-32` - unify query/history filter semantics.
- `DIS-33` - update docs and skills.

## Source Files

- `src/outfitter/dispatch/core/models.py` - `SearchInput`, `HistoryInput`, and output models.
- `src/outfitter/dispatch/core/handlers.py` - current `search` and `history` handlers.
- `src/outfitter/dispatch/registry/store.py` - `thread_items`, `thread_item_refs`, and query helpers.
- `src/outfitter/dispatch/core/history.py` - history item extraction/filtering.
- `src/outfitter/dispatch/core/history_index.py` - normalized history indexing and ref extraction.
- `src/outfitter/dispatch/core/ops.py` - op registry definitions.
- `src/outfitter/dispatch/contracts/derive_cli.py` - CLI projection.
- `src/outfitter/dispatch/contracts/derive_mcp.py` - MCP projection.

## Docs / ADRs / Notes

- `docs/development/design.md` - architecture and current search/history description.
- `docs/adrs/0018-top-level-thread-actions-and-search.md` - top-level action/search framing.
- `docs/adrs/0023-provider-event-log-and-history-index.md` - local normalized substrate decision.
- `docs/development/semantic-history-search.md` - future semantic search framing.
- `docs/usage/README.md` - operator docs needing grammar updates.
- `skills/dispatch/SKILL.md` - first-party operator skill needing grammar updates.
- `skills/dm/SKILL.md` - check whether any examples mention search/query behavior.

## PRs / Branches

- None yet.

## Commands

- `uv run dispatch schema search` - proves App Server search schema.
- `uv run dispatch schema query` - proves local query schema after implementation.
- `uv run dispatch query --tool linear.save_issue --limit 5 --json` - proves concrete tool-call discovery.
- `uv run dispatch search sqlite --limit 5 --json` - proves App Server search remains available.
- `uv run ruff check .` - lint.
- `uv run ruff format --check .` - formatting.
- `uv run mypy src tests` - typecheck.
- `uv run pytest` - tests.
- `just check` - repo gate.

## Prompt

- `.agents/goals/2026-07-02-query-split-filters/PROMPT.md` - initial prompt used to start or resume the goal.

## Review Reports

- `.agents/goals/2026-07-02-query-split-filters/tmp/reviews/` - scratch location for review reports during execution.
