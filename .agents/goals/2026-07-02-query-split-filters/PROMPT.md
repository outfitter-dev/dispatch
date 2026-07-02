/goal In `/Users/mg/Developer/outfitter/dispatch`, execute `.agents/goals/2026-07-02-query-split-filters` to merged.

## Read First
- `AGENTS.md`, `SPEC.md`, `GOAL.md`, `REFS.md`
- Linear: `DIS-20`, `DIS-29`-`DIS-33`, adjacent `DIS-28`

## Objective
Split App Server `dispatch search` from local indexed `dispatch query`; make `query` the structured substrate surface for filters and concrete tool-call discovery.

## Authority
- Commit, push, open PRs, mark ready, merge, and update Linear for `DIS-29`-`DIS-33`.
- Do not publish/release.
- Ask first before backend-default changes, paid/cloud services, large raw-result indexing, or horizon changes.

## Boundary
- In: contracts, CLI/MCP projections, handlers, registry helpers, tests, docs, skills, Linear, `RETRO.md`.
- Out: semantic/vector search, Turso migration, multi-machine sync, remote query service, publishing.
- Do not touch secrets, user-level config, unrelated provider work, or live data except read-only smoke queries.

## Sequence
1. `DIS-29`: add first-class `dispatch query`; keep `search` App Server-only; remove canonical `search --local`.
2. `DIS-30`/`DIS-32`: add indexed filters and shared query/history matching semantics.
3. `DIS-31`: promote safe concrete tool metadata so `dispatch query --tool linear.save_issue` finds real calls.
4. `DIS-33`: update docs, skills, MCP guidance, examples, and jq/schema notes.
5. Review, merge, sync `main`, update Linear, and finalize `RETRO.md`.

## Loop
For each slice: implement narrowly, focused tests, local review, fix P0/P1/P2 and cheap P3s, broaden checks, update `RETRO.md`, continue. Use subagents for bounded audits/reviews; keep final decisions centralized.

## Verification
- Packet: `check-goal-prompt --no-placeholders .agents/goals/2026-07-02-query-split-filters/PROMPT.md`; `goal-loop-doctor .agents/goals/2026-07-02-query-split-filters`.
- Focused tests: registry query helpers, handler validation, CLI derivation, MCP projection, docs/schema examples.
- Gates: `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src tests`; `uv run pytest`; `just check`.
- Smoke: `uv run dispatch schema search`; `uv run dispatch schema query`; `uv run dispatch query --tool linear.save_issue --limit 5 --json`; `uv run dispatch search sqlite --limit 5 --json`.

## Hard Rules
- `query` must not be an alias for `search --local`.
- Preserve contract-first derived CLI/MCP surfaces.
- No unresolved P0/P1/P2 before merge.
- Do not index sensitive or large raw tool result bodies by default.
- Do not change storage backend defaults.

## Stop Rules
Stop only if App Server behavior is unavailable, authority is missing, storage/privacy needs Matt, or safe implementation needs paid/cloud access.

## Evidence Contract
Record PRs, checks, reviews, smoke output, Linear state, docs/skills, risks, and forbidden-action audit in `RETRO.md` and final chat.

## Next Move
If checks fail, narrow and fix. If scope balloons, land the smallest issue-sized slice and track the rest. Ask Matt only for storage/privacy/product decisions.

## Definition Of Done
- `DIS-29`-`DIS-33` are done or reconciled in Linear with evidence.
- PRs are merged, `main` is synced and clean, and local/CI checks are green.
- `dispatch search` and `dispatch query` have distinct schemas/help/behavior.
- Docs and skills teach `search`, `query`, `history`, and `sync` distinctly.
- `RETRO.md` includes checks, reviews, PRs, Linear, smoke, risks, and forbidden-action audit.

## Not Done
Draft PRs, an alias, docs-only changes, query without tests, raw-spelunking-only tool filters, or unmerged work do not satisfy this goal.

## Persistence
Use this packet and `RETRO.md` as the resume surface; keep going through CI/review/merge waits until done or stopped.

Keep going until done unless a stop rule fires.
