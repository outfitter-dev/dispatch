/goal From `/Users/mg/Developer/outfitter/dispatch`, execute `.agents/goals/2026-07-02-local-substrate-stack` to the `merged` horizon.

Read first: `AGENTS.md`, the packet `SPEC.md`/`GOAL.md`/`REFS.md`, `docs/development/local-substrate-roadmap.md`, `docs/development/cloud-gateway.md`, ADR-0013, ADR-0014, ADR-0023, and `docs/research/turso-libsql-storage-spike.md`.

## Objective
Land as much of the local substrate stack as safely possible: roadmap/tracker setup, gateway boundary, storage boundary, event-ingest harness, semantic-search policy, and multi-machine selected-state sync design.

## Authority
May commit, push, open PRs, mark ready, merge, and update Linear `DIS-20` through `DIS-25`. Do not release/publish, make Turso default, use live user data, create cloud credentials, call paid embeddings/APIs, or implement a real gateway runtime.

## Boundary
In: docs, ADR/research notes, goal packet, Linear updates, small storage/test/harness code, synthetic fixtures. Out: real `~/.codex`, secrets, live sync, real embeddings, unrelated repos/branches.

## Sequence
1. Roadmap/gateway boundary: land roadmap note, tracker links, and gateway non-log-sink wording.
2. Storage boundary: add the smallest useful connection/transaction or contract-test slice, or precisely defer.
3. Event ingest: add synthetic opt-in load harness/metrics, or precisely defer.
4. Semantic search: define derived-artifact indexing and retention policy, with fake-data prototype only if safe.
5. Multi-machine sync: update docs/ADR for selected-state sync, durable queues, and gateway boundary.

## Loop
For each milestone: make the smallest scoped change; run focused checks; run local-review style review; fix all P0/P1/P2; update `RETRO.md`/`REFS.md`/Linear; then continue or defer with reasons.

## Verification
Run `check-goal-prompt --no-placeholders` and `goal-loop-doctor` for the packet. Run `uv run ruff check .` and `uv run ruff format --check .` for docs/code. Run `uv run python -m pytest tests/registry -q` after storage changes. Run `uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py` after storage SQL/probe changes. Run `just check` before ready/merge for product code or broad stack changes.

## Evidence Contract
Record Linear issue IDs, changed files, PRs, exact checks, review reports/scores, open P0/P1/P2 count, deferred scope, forbidden-action audit, and final `main` sync proof.

## Hard Rules
SQLite/`aiosqlite` remains the default. Gateway routes intent, not logs. Use synthetic data for probes. Keep milestones small and reviewable. Do not move forward with unresolved P0/P1/P2.

## Next Move
If checks fail, isolate and fix the smallest failing scope. If scope sprawls, defer the excess to Linear and continue with the next safe milestone. If forbidden authority is needed, stop and record the blocker.

## Stop Rules
Stop only if useful progress requires real user data, secrets, cloud credentials, paid APIs, default backend migration, unresolved unrelated check failures, or overlapping user changes.

## Definition Of Done
Completed feasible milestones are merged; deferred milestones are explained in Linear/`RETRO.md`; no unresolved P0/P1/P2 remains; PR stack is merged; local `main` is clean/synced.

## Not Done
Chat-only roadmap, stale Linear issues, draft PRs, local-only checks, or open P0/P1/P2 is not done.

## Persistence
Keep `RETRO.md` current. Resume from the current branch, `RETRO.md`, and Linear `DIS-20` children.

Keep going until done or a stop rule fires.
