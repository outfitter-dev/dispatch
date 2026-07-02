## Objective

/goal In /Users/mg/Developer/outfitter/dispatch, execute `.agents/goals/2026-07-02-search-baselines-turso`: land `DIS-23` local/semantic history search substrate, `DIS-26` synthetic ingestion baselines, and `DIS-27` Turso/libSQL decision memo.

## Authority

Completion horizon: merged. You may commit, push, open PRs, mark ready, merge, and update Linear `DIS-20`, `DIS-23`, `DIS-26`, and `DIS-27`.

## Boundary

In: local registry search, synthetic baselines, Turso decision docs, tests, generated CLI/MCP schema/help updates. Out: paid embeddings/APIs, cloud credentials, real transcript embeddings, live/private benchmark data, default backend migration, and `DIS-24` implementation.

## Sequence

1. `DIS-23`: add the smallest useful local history search path over normalized registry history or derived artifacts. Preserve broad App Server search as default unless explicit local mode is requested.
2. `DIS-26`: run at least three named synthetic ingestion profiles with `scripts/measure_event_ingestion.py`; record exact commands, results, and limitations.
3. `DIS-27`: write an evidence-based Turso/libSQL decision memo comparing SQLite/aiosqlite vs Turso/libSQL for local search, vector search, ingestion, async boundary, packaging, security/config, and future sync.

## Loop

For each milestone: implement -> focused tests -> local review -> fix P0/P1/P2 and cheap P3 -> `just check` -> PR ready/merge -> update `RETRO.md` and Linear.

## Hard Rules

No paid calls, no cloud credentials, no live/private data, no backend default change, no vague Turso recommendation, and no unresolved P0/P1/P2 before merge.

## Stop Rules

Stop only for a failing 0.8.1 release/publish state, missing source-control/tracker authority, required paid/cloud access, or a default-backend decision needing Matt.

## Definition Of Done

Relevant PRs merged, `main` synced and clean, focused checks plus `just check` pass, local reviews are 5/5 or only accepted residual P3s, and Linear reflects done/deferred work.

## Evidence Contract

Final report must include merged PRs, checks, review report paths, Linear states, baseline commands/results, Turso recommendation, risks, and forbidden-action audit.

## Next Move

Start with `DIS-23`. If semantic search gets too broad, ship structured local keyword search and policy first; track embeddings/vector search separately.

## Not Done

Draft PRs, policy-only search, non-rerunnable baselines, or a hand-wavy Turso memo do not satisfy the goal.

## Persistence

Use `.agents/goals/2026-07-02-search-baselines-turso/RETRO.md` as the ledger and `tmp/reviews/` for scratch review reports.
