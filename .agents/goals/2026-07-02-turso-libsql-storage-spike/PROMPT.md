/goal From `/Users/mg/Developer/outfitter/dispatch`, execute `.agents/goals/2026-07-02-turso-libsql-storage-spike` to the `merged` horizon.

Read first: `AGENTS.md`, packet `SPEC.md`/`GOAL.md`/`REFS.md`, `docs/adrs/0023-provider-event-log-and-history-index.md`, `.agents/goals/2026-07-02-history-load-shape/RETRO.md`, `src/outfitter/dispatch/registry/store.py`, `tests/registry/`, `tests/fixtures/`, and current official Turso docs for libSQL, Python quickstart, Sync, and AI/Embeddings.

## Objective
Decide with evidence whether Dispatch should adopt Turso/libSQL now, later behind an optional boundary, or not at all for registry/history storage. Record the final decision in repo docs and Linear `DIS-8`.

## Authority
Commit, push, open PR, mark ready, and merge after clean checks/reviews. Do not release, publish, make Turso default, create/use Turso Cloud credentials, mutate real `~/.codex` or user registry data, add paid services, or drop `just check` without asking Matt.

## Boundary
In: docs, this packet, `spikes/`, focused registry tests/fixtures, tiny storage-boundary code if proven useful. Out: live state, cloud credentials, release/publish, broad provider work, remote gateway, generalized DB framework.

## Sequence
1. Baseline: answer the nine decision questions in `RETRO.md` before coding.
2. Shape: choose the smallest proof: docs-only, spike script, fixture-backed adapter, or tiny production-safe boundary.
3. Probe: run local `pyturso`/`libsql` package/API probes or record precise incompatibility.
4. Decide: update ADR/research/docs/skills only where evidence makes guidance stale.
5. Review/merge: local review loop, focused checks, `just check`, PR, merge, synced `main`.

## Loop
For each item: execute the smallest scoped change; review for P0/P1/P2; verify; update `RETRO.md` and `REFS.md`; continue only when evidence is recorded and no P0/P1/P2 remains.

Checks: `uv run python -m pytest tests/registry -q`; `uv run python -m pytest tests/fixtures tests/registry -q` if fixtures change; `uv run --with pyturso python -c "import turso; print('pyturso-ok')"` if feasible; `uv run --with libsql python -c "import libsql; print('libsql-ok')"` if useful without credentials; `just check` before ready/merge.

## Evidence Contract
Record initial, post-probe, and final answers to: what Turso solves today; smallest storage boundary; first relevant path (`pyturso`, `libsql`, Sync, vector-only, none); async daemon fit; packaging/install risk; fixture parity; vector timing; cloud/sync security scope; revisit trigger if deferred.

## Next Move
If checks fail, isolate the smallest failing test and fix or record the exact blocker. If progress stalls, stop adding abstraction and choose the smallest proof that can change the decision. If scope is unclear, prefer docs/research/probe and keep SQLite default.

## Hard Rules
SQLite/`aiosqlite` remains default. Prefer temporary probes before deps. No live user data. No real cloud credentials. No broad abstraction unless proof demands it.

## Stop Rules
Stop only if useful proof requires credentials/paid resources, default-backend migration, unsafe unrelated user changes, or an unrelated `just check` blocker.

## Definition Of Done
Final recommendation is in docs and `RETRO.md`; `DIS-8` has an outcome comment; focused proof is committed; checks are green; no unresolved P0/P1/P2; PR is merged; local `main` is clean/synced.

## Not Done
Chat-only conclusion, local-only note, draft PR, dependency change without packaging evidence, or open P0/P1/P2 is not done.

## Persistence
Keep `RETRO.md` current after each major step. Resume from branch plus `RETRO.md` "Next" if interrupted.

Keep going until done or a stop rule fires.
