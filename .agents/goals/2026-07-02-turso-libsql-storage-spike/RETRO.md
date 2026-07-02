# Execution Retro: turso-libsql-storage-spike

Date started: 2026-07-02
Date finalized: pending
Status: ready to merge
Spec: `.agents/goals/2026-07-02-turso-libsql-storage-spike/SPEC.md`
Goal: `.agents/goals/2026-07-02-turso-libsql-storage-spike/GOAL.md`
Prompt: `.agents/goals/2026-07-02-turso-libsql-storage-spike/PROMPT.md`
Refs: `.agents/goals/2026-07-02-turso-libsql-storage-spike/REFS.md`

## Summary

- Objective: Decide whether Dispatch should adopt Turso/libSQL now, later behind an optional boundary, or not at all.
- Completion horizon: merged.
- Authority used: Created branch, committed/pushed scoped changes, opened PR #57, updated Linear `DIS-8`; no release, publish, default-backend migration, live data mutation, or real Turso credentials.
- Outcome: keep SQLite/`aiosqlite` as the default; retain Turso/libSQL as optional future backend candidate behind a smaller storage boundary.
- Tracker/PR/source-control state: PR #57 is open and CI green; Linear `DIS-8` updated with outcome comment.
- Verification: goal prompt and packet validation passed; initial Python package probes passed.
- Review state: full-stack local review clean, 5/5, no P0/P1/P2.
- Remaining risks: Turso docs/packages are moving; package probes may require network and may fail on platform wheels; sync/cloud cannot be fully proven without credentials and must stay out of scope.

## Readiness

- Prompt checked: pass, 3715/4000 characters, no unresolved placeholders.
- Goal/prompt alignment checked: pass.
- Review blockers: none known.
- Verification blockers: none known.
- Tracker blockers: richer Linear read tooling was not available in the current surface; update/comment is available.
- Authority blockers: no release/publish authority; no default-backend migration authority.
- Next action: mark PR #57 ready, merge, sync `main`, and finalize this goal.

## Decision Question Passes

### Initial Pass

1. What does Turso solve today that SQLite/`aiosqlite` does not after the history-load fixes?
   - Initial answer: not an urgent correctness gap. The current store uses `aiosqlite`, a write lock, WAL, `busy_timeout`, transactional snapshot writes, and DB-backed history summary/item reads. Turso may help future multi-writer pressure, vector search, and sync, but the immediate post-fix pain is more "store shape is large" than "SQLite cannot work."

2. Is a small storage boundary possible without a database-framework project?
   - Initial answer: maybe, but not around the whole 2k-line `Registry` at once. The likely seam is a lower-level connection/transaction protocol or a narrow history-index protocol, because current code exposes `aiosqlite.Row`, direct SQL, migrations, and `_conn` test probes.

3. Which path matters first?
   - Initial answer: `pyturso` local embedded first for drop-in local store feasibility. `libsql` remote/embedded-replica and Turso Sync are secondary because they introduce remote URL/auth/sync semantics. Vector search is promising but should remain an optional index over normalized `thread_items`/summaries.

4. Does the Python API fit the async daemon?
   - Initial answer: not directly. `pyturso` imports and behaves sqlite3-like but exposes synchronous `connect/execute/commit` and no obvious async connect. `libsql` is also synchronous in the local probe. A production adapter would need `run_in_executor`, a dedicated writer thread, or a different async package path; this is a real design constraint.

5. What are the dependency, wheel, platform, and install risks?
   - Initial answer: both packages install under `uv --with` on local Python 3.13 (`pyturso 0.6.1`, `libsql 0.1.11`). That proves this machine, not all supported platforms. A default dependency would still need wheel/platform audit and release packaging proof.

6. Can current registry/fixture tests run against a second backend meaningfully?
   - Initial answer: not without either adapter work or test refactoring. Tests currently inspect `store._conn` directly, use `aiosqlite` types/exceptions, and seed old SQLite schemas. A second backend fixture can start with selected behavior tests, but full parity is not plug-in ready.

7. Does vector search belong now?
   - Initial answer: no as a production feature. Official docs show native vector functionality, but Dispatch first needs a clean normalized item/summarization index and an embedding policy. This spike can document feasibility, not ship vector search.

8. Does cloud/sync require security/config scope outside v0?
   - Initial answer: yes. Turso Sync needs remote URL and auth token semantics, plus bootstrap/conflict/checkpoint decisions. That is valuable for future mesh/cloud gateway work, but outside a local default registry change.

9. If deferred, what trigger should reopen the decision?
   - Initial answer: revisit when Dispatch has a stable storage boundary plus one of these: measured SQLite write contention under realistic history/event ingest, a committed semantic-search feature needing local vector indexes, or an explicit multi-machine sync requirement with a credential/config design.

### Prototype Pass

1. What does Turso solve today that SQLite/`aiosqlite` does not after the history-load fixes?
   - Prototype answer: the current schema and representative history writes run on SQLite, `pyturso`, and `libsql` after a small SQL portability hardening. That lowers future migration risk but still does not prove an immediate need to replace `aiosqlite`.

2. Is a small storage boundary possible without a database-framework project?
   - Prototype answer: yes, but the first boundary should be SQL compatibility and connection/transaction behavior, not a wholesale `Registry` interface split. The committed probe is a reusable compatibility gate for that boundary.

3. Which path matters first?
   - Prototype answer: `pyturso` local embedded is the relevant first local-store probe; `libsql` is also compatible for the representative SQL. Turso Sync remains separate because credentials/conflict policy/checkpointing are product decisions.

4. Does the Python API fit the async daemon?
   - Prototype answer: both packages still look synchronous from local probes. They can be tested for SQL compatibility now, but a production default would need an async integration design.

5. What are the dependency, wheel, platform, and install risks?
   - Prototype answer: local Python 3.13 install/import works. Broader packaging remains unproven and should be a release-readiness gate before any default dependency.

6. Can current registry/fixture tests run against a second backend meaningfully?
   - Prototype answer: representative SQL can run against a second backend now. Full registry test parity still needs refactoring because tests and `Registry` are tied to `aiosqlite` and direct `_conn` access.

7. Does vector search belong now?
   - Prototype answer: no. The storage probe is independent of embeddings. Vector search should wait for an embedding policy and optional index design.

8. Does cloud/sync require security/config scope outside v0?
   - Prototype answer: yes. Nothing in the local probe changes that.

9. If deferred, what trigger should reopen the decision?
   - Prototype answer: same as initial, plus this new concrete trigger: when Dispatch has a connection/transaction adapter for the registry, run the committed probe plus selected registry behavior tests against `pyturso`/`libsql`.

### Final Pass

1. What does Turso solve today that SQLite/`aiosqlite` does not after the history-load fixes?
   - Final answer: no immediate default-backend problem. Turso remains valuable for possible future concurrency, vector search, and multi-machine sync, but current SQLite is adequate after stabilization.

2. Is a small storage boundary possible without a database-framework project?
   - Final answer: yes, but start with connection/transaction and SQL compatibility boundaries. Do not abstract the whole `Registry` yet.

3. Which path matters first?
   - Final answer: `pyturso` local embedded is the first backend candidate for local registry feasibility. `libsql` and Turso Sync are later remote/sync candidates. Vector search is a separate optional index.

4. Does the Python API fit the async daemon?
   - Final answer: not directly yet. Local probes show synchronous APIs; any production integration needs a dedicated async strategy.

5. What are the dependency, wheel, platform, and install risks?
   - Final answer: local Python 3.13/macOS install/import works, but cross-platform wheel and release-package behavior remain unproven.

6. Can current registry/fixture tests run against a second backend meaningfully?
   - Final answer: representative SQL can run via the committed spike. Full parity needs test/store refactoring away from direct `aiosqlite` coupling.

7. Does vector search belong now?
   - Final answer: no. It should follow embedding and retention policy work.

8. Does cloud/sync require security/config scope outside v0?
   - Final answer: yes. Turso Sync/cloud belongs to future mesh/cloud-gateway configuration work.

9. If deferred, what trigger should reopen the decision?
   - Final answer: reopen when Dispatch has a small connection/transaction boundary plus measured SQLite contention, a committed semantic-search requirement, or an explicit multi-machine sync requirement.

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-02 TBD | Initial packet created. | Start Turso/libSQL storage spike with explicit decision gates. | Matt |

## Execution Log

```text
2026-07-02 TBD - Packet setup
- Changed: Created goal packet files for the Turso/libSQL storage spike.
- Verified: `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders .agents/goals/2026-07-02-turso-libsql-storage-spike/PROMPT.md` passed; `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor .agents/goals/2026-07-02-turso-libsql-storage-spike` passed.
- Result: Goal contract validated.
- Next: Begin Step 1 decision baseline.
- Blockers: None known.

2026-07-02 TBD - Initial baseline and package probes
- Changed: No product code. Read current SQLite store/history shape and ran temporary package probes for `pyturso` and `libsql`.
- Verified: `uv run --with pyturso python -c "import turso; print('pyturso-ok')"` passed; `uv run --with libsql python -c "import libsql; print('libsql-ok')"` passed; deeper API probes showed synchronous sqlite-like APIs and no obvious async connect path.
- Result: Initial decision pass recorded. Next proof should be a bounded compatibility/adapter spike, not a default backend migration.
- Next: Build a small spike artifact that exercises schema/transaction/history operations against SQLite and `pyturso` or records precisely why parity is not safe yet.
- Blockers: None known.

2026-07-02 TBD - Compatibility probe and SQL portability hardening
- Changed: Added `spikes/06_turso_libsql_storage_probe.py`; changed provider-event dedupe SQL from partial-index-targeted `ON CONFLICT(provider, provider_event_id) WHERE provider_event_id IS NOT NULL DO NOTHING` to portable `ON CONFLICT DO NOTHING`; added a regression test proving FK violations are not ignored.
- Verified: Focused provider-event tests passed; storage probe passed on stdlib SQLite, `pyturso`, and `libsql`; full `tests/registry` passed.
- Result: Representative registry/history SQL is now compatible across all three local probes, while the spike still records that `pyturso` does not support the old partial conflict target form.
- Next: Update ADR/research docs with the decision matrix and recommended adoption path.
- Blockers: None known.

2026-07-02 TBD - Docs and local review
- Changed: Added `docs/research/turso-libsql-storage-spike.md`; updated ADR-0023 with the spike result; wrote local review report.
- Verified: `just check` passed; JSON review report validates with `python -m json.tool`.
- Result: Final decision recorded in repo docs; local review is clean at 5/5.
- Next: Commit, push, open PR, update Linear `DIS-8`, and merge when clean.
- Blockers: None known.

2026-07-02 TBD - PR and tracker
- Changed: Submitted PR #57 and updated Linear `DIS-8` with the outcome, checks, and follow-ups.
- Verified: GitHub checks for PR #57 passed: `check`, `Analyze (actions)`, `Analyze (python)`, and `CodeQL`.
- Result: PR is ready to leave draft.
- Next: Amend this retro update, resubmit, mark ready, merge, and sync `main`.
- Blockers: None known.
```

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| full-stack-round-1 | branch/docs/spike/store change | `.agents/goals/2026-07-02-turso-libsql-storage-spike/tmp/reviews/full-stack-round-1.json` | 5/5 | clean | 0 | No findings; residual risks are packaging/platform, async integration, and full backend parity boundary. |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |
| `check-goal-prompt --no-placeholders` | prompt concreteness | pass | 3715/4000 characters; no unresolved placeholders. |
| `goal-loop-doctor` | packet consistency | pass | Required sections present; no review reports yet. |
| `uv run --with pyturso python -c "import turso; print('pyturso-ok')"` | package import | pass | Installed/imported `pyturso 0.6.1` on local Python 3.13. |
| `uv run --with libsql python -c "import libsql; print('libsql-ok')"` | package import | pass | Installed/imported `libsql 0.1.11` on local Python 3.13. |
| `uv run --with pyturso python - <<'PY' ...` | API shape | pass | `turso.connect(':memory:')` works; connection exposes sqlite-like sync methods; no obvious async connect. |
| `uv run --with libsql python - <<'PY' ...` | API shape | pass | `libsql.connect(':memory:')` works; cursor requires `fetchall`; no obvious async connect. |
| `uv run python -m pytest tests/registry/test_store.py::test_provider_event_history_index_roundtrips_and_dedupes tests/registry/test_store.py::test_provider_event_foreign_key_errors_are_not_ignored -q` | provider-event dedupe/FK behavior | pass | 2 passed. |
| `uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py` | cross-backend SQL compatibility | pass | sqlite3 and libsql support old partial conflict target; pyturso does not, but portable SQL passes all three. |
| `uv run python -m pytest tests/registry -q` | registry storage suite | pass | 34 passed. |
| `just check` | full repo gate | pass | ruff, format check, mypy, pytest 386 passed / 9 deselected, build, package contents check. |
| `uv run python -m json.tool .agents/goals/2026-07-02-turso-libsql-storage-spike/tmp/reviews/full-stack-round-1.json >/dev/null` | review report JSON | pass | Report is valid JSON. |

## Prompt / Goal Alignment

- Checked by: Codex.
- Result: pass.
- Missing from prompt: none after revision.
- Fixes made: Shortened prompt below 4000 characters, removed placeholder-like shorthand, and added canonical `Boundary`, `Definition Of Done`, `Evidence Contract`, `Next Move`, `Not Done`, and `Persistence` sections.

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| DIS-8 | updated | Outcome comment posted with PR #57, checks, decision, and follow-ups. |
| PR #57 | open, draft, CI green | https://github.com/outfitter-dev/dispatch/pull/57 |

## Follow-Ups

- pending.

## Final State

- Completion proof: pending merge.
- Prompt length: 3715/4000 characters.
- Review report summary: full-stack local review, 5/5 clean, no findings.
- Verification summary: local `just check` passed; PR #57 GitHub checks passed.
- Forbidden actions audit: no release/publish, no default backend switch, no real Turso credentials, no live user registry or `~/.codex` mutation.
- Remaining P3s / risks: platform wheel audit, async production integration, and full alternate-backend parity remain follow-up scope.
- Final transcript proof: pending merge and local `main` sync.
