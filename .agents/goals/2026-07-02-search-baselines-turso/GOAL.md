# Goal: search-baselines-turso

## Completion Horizon

Merged. The loop is complete when the selected milestone PRs are merged, Linear
is updated, and local `main` is synced and clean.

## Authority

May commit, push, open PRs, mark ready, merge, and update Linear issues
`DIS-20`, `DIS-23`, `DIS-26`, and `DIS-27`.

May not make paid embedding/API calls, create cloud credentials, migrate user
data, make Turso/libSQL the default backend, or implement `DIS-24`.

## Boundary

In scope: local search over normalized registry history or derived artifacts,
synthetic ingestion baselines, Turso/libSQL decision documentation, docs, tests,
and tracker updates.

Out of scope: real transcript embeddings, live/private benchmark data,
multi-machine sync implementation, remote Turso databases, and default backend
migration.

## Topology

Milestone stack. Prefer one branch/PR per milestone if the diffs are meaningful:
`DIS-23`, then `DIS-26`, then `DIS-27`.

## Steps

1. Local search substrate (`DIS-23`)
   - Add the smallest useful local history search path over registry history or
     derived artifacts.
   - Preserve broad App Server search as the default unless explicit local mode
     is requested.
   - Update CLI/MCP schemas through the existing contract layer.
   - Gate: focused search/registry/surface tests, docs, local review, `just check`.

2. Ingestion baselines (`DIS-26`)
   - Run named synthetic profiles through `scripts/measure_event_ingestion.py`.
   - Record commands, results, and limitations in a durable note.
   - Gate: rerunnable commands, docs review, relevant checks.

3. Turso/libSQL decision (`DIS-27`)
   - Compare SQLite/aiosqlite and Turso/libSQL using the search and baseline
     evidence.
   - State the next recommendation and follow-up issues if needed.
   - Gate: decision review, docs checks, tracker updates.

## Reviews

Use targeted local review after each milestone. Fix P0/P1/P2 before moving on.
Fix reasonable P3s when cheap; otherwise record them.

## Verification

- Focused tests for changed search, registry, and surface behavior.
- Baseline commands recorded with exact parameters and JSON results.
- `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders .agents/goals/2026-07-02-search-baselines-turso/PROMPT.md`
- `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor .agents/goals/2026-07-02-search-baselines-turso`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src tests`
- `uv run pytest`
- `just check`

## Evidence Contract

`RETRO.md` must record issue/PR state, exact checks, review report paths, final
recommendations, accepted residual risk, and forbidden-action audit. Linear
comments should summarize landed evidence for completed issues.

## Next Move

Start with `DIS-23`. If the local search implementation balloons, cut it down
to structured keyword search over normalized history and document embedding work
as a follow-up.

## Waiting State

External waits are limited to CI, merge queue, and GitHub/Linear state checks.
Poll with `gh pr view`, `gh run watch`, and `gt sync`; do not leave running
commands dangling.

## Persistence

Use `.agents/goals/2026-07-02-search-baselines-turso/RETRO.md` as the execution
ledger and `.agents/goals/2026-07-02-search-baselines-turso/tmp/reviews/` for
scratch review reports.

## Done

- `DIS-23`, `DIS-26`, and `DIS-27` are either done or explicitly deferred with
  evidence in Linear and this retro.
- All landed slices pass focused checks, `just check`, CI, and local review with
  no unresolved P0/P1/P2.
- `main` is synced and clean.

## Not Done

- Draft PRs only.
- Search policy without tests.
- Baselines that cannot be rerun.
- Turso recommendation without concrete local evidence.
- Any implementation requiring paid embeddings, cloud credentials, or live
  private data.

## Stop Rules

Stop only for: failing release/publish state from `0.8.1`, missing authority for
tracker/PR mutations, required cloud credentials/paid APIs, or a storage/search
decision that would change the default backend.
