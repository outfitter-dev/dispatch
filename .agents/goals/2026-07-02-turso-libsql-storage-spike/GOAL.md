# Goal Execution Contract: turso-libsql-storage-spike

Date: 2026-07-02
Status: Active
Spec: `.agents/goals/2026-07-02-turso-libsql-storage-spike/SPEC.md`
Prompt: `.agents/goals/2026-07-02-turso-libsql-storage-spike/PROMPT.md`
Retro: `.agents/goals/2026-07-02-turso-libsql-storage-spike/RETRO.md`
Refs: `.agents/goals/2026-07-02-turso-libsql-storage-spike/REFS.md`

## Completion Horizon

merged

Complete when:

- The Turso/libSQL adoption decision is evidence-backed, recorded in repo docs, reflected on Linear `DIS-8`, and merged to `main`.
- Any supporting spike code, fixture, benchmark, or boundary proof is committed, tested, and reviewed.
- The final state keeps SQLite/`aiosqlite` as the default backend unless a follow-up migration is separately scoped.

Not complete when:

- The result is only a chat summary with no repo artifact.
- The result says "Turso seems good" without answering the decision questions.
- Code changes exist without docs/ADR/retro updates.
- Local checks or review loops still have unresolved P0/P1/P2 findings.
- The PR is open but unmerged, unless merge is externally blocked and recorded.

## Authority

- May commit: yes, scoped to this branch and this goal.
- May push: yes, for PR creation/update.
- May open PR: yes.
- May mark ready: yes, after checks and review are clean.
- May merge: yes, if CI/reviews are clean and no user approval boundary is crossed.
- May publish/release: no.
- Needs user approval for: making Turso/libSQL the default backend, creating Turso Cloud credentials, mutating real user registry data, adding paid external services, changing the completion horizon, or dropping `just check`.

## Boundary

- In scope: `docs/`, `.agents/goals/2026-07-02-turso-libsql-storage-spike/`, `spikes/`, narrowly scoped registry tests/fixtures, narrowly scoped storage-boundary code if the proof shows it is worthwhile.
- Out of scope: PyPI release, live Codex/Claude state mutation, Turso Cloud setup using real credentials, broad provider work, remote gateway work, generalized database framework work.
- Do not touch: user global Codex/Claude config, real `~/.codex` data, secrets, unrelated branches, unrelated PRs, generated artifacts not caused by this goal.

## Topology

Single-agent direct execution with bounded subagents for source research, storage-shape review, and local review when useful. Use one branch first. Split into a stack only if the work naturally separates into a docs-only decision PR plus a code-spike PR.

## Steps

1. Decision baseline
   - Outcome: The goal runner has a current answer hypothesis for all nine decision questions before writing code.
   - Scope: Read ADR-0023, prior history-load-shape retro, current store/schema/tests, Linear `DIS-8`, and official Turso docs for libSQL, Python, sync, and vectors.
   - Gate: `RETRO.md` records the initial decision-question pass plus citations/paths in `REFS.md`.

2. Storage boundary and prototype shape
   - Outcome: The runner chooses the smallest proof: no boundary needed, spike-only adapter, fixture-backed second backend, or production-safe boundary scaffold.
   - Scope: Inspect `src/outfitter/dispatch/registry/store.py`, transaction helpers, model boundaries, fixture builders, and registry tests.
   - Gate: `RETRO.md` explains why the chosen proof is the smallest useful proof and explicitly rejects over-generalized abstraction if applicable.

3. Practical Turso/libSQL probe
   - Outcome: A practical local probe compares the current SQLite path with the relevant Turso/libSQL path, or records a precise incompatibility that prevents a useful probe.
   - Scope: Prefer temporary `uv run --with pyturso` / `uv run --with libsql` probes or committed `spikes/turso_libsql/` scripts/tests that do not add production dependencies unless justified.
   - Gate: The probe output answers API fit, event-loop blocking, migration compatibility, test-fixture reuse, and vector/sync relevance.

4. Decision and repo updates
   - Outcome: Docs state the final recommendation and any implementation follow-ups clearly.
   - Scope: Update ADR-0023 or add a focused research note/ADR addendum, update usage/development docs only if current guidance becomes stale, and update skills only if agent instructions change.
   - Gate: `RETRO.md` contains a decision matrix and final recommendation; `DIS-8` has a concise outcome comment.

5. Review, verify, submit, and merge
   - Outcome: The PR is reviewed, green, merged, and local `main` is clean.
   - Scope: Run focused checks first, then `just check`; run local review loops until no unresolved P0/P1/P2 findings; push/open PR; merge only after gates are clean.
   - Gate: `RETRO.md` final state lists exact checks, review results, PR/merge state, risks, and forbidden-action audit.

## Reviews

- Use local-review style for at least one full-stack review before ready/merge.
- For code or dependency changes, request a targeted storage/async review focused on event-loop blocking, migration safety, and test coverage.
- Fix all P0/P1/P2 findings. Fix easy P3s when they improve clarity without scope creep. Record accepted/deferred P3s in `RETRO.md`.

## Evidence Contract

- Initial, mid, and final answers to the nine decision questions.
- Official Turso source citations or links used for current package/API/sync/vector claims.
- Current Dispatch source paths and tests used as evidence.
- Prototype or failed-probe commands and results.
- Final recommendation with adoption timing and revisit trigger.
- PR, checks, review, Linear, and final git state.

## Verification

- `uv run python -m pytest tests/registry -q` after storage-touching changes.
- `uv run python -m pytest tests/fixtures tests/registry -q` if fixture builders or cross-backend fixtures are touched.
- `uv run --with pyturso python - <<'PY'` probe for local embedded API fit, if package installation is feasible.
- `uv run --with libsql python - <<'PY'` probe for remote/embedded-replica API facts, if useful without credentials.
- `just check` before ready/merge.
- Prompt/goal alignment: `RETRO.md` must state whether `PROMPT.md` contains sequence, loop shape, concrete checks, review gate, stop rules, done/not-done states, and persistence plan.

## Next Move

- If a check fails: narrow to the smallest failing focused test, fix or record the exact blocker, then rerun the focused check and `just check`.
- If progress stalls: stop adding abstraction, write down the current decision-question answers, and choose the smallest proof that can change the recommendation.
- If scope is unclear: default to docs/research/probe over production migration and keep SQLite as the default.

## Waiting State

- Waiting on: CI, review feedback, PR mergeability, or external package availability only.
- How to check: `gh pr checks`, `gh pr view`, `gt log --no-interactive`, and focused package probe commands.
- Heartbeat cadence: none needed unless a PR/CI wait exceeds 20 minutes.
- Continue when: checks/reviews are clear or a narrower local failure is understood.
- Stop when: a required external credential, paid service, default-backend migration, or destructive live-data action becomes necessary.
- Last checked: not started.

## Persistence

- Keep `RETRO.md` current after each major step.
- Add source links and command proof to `REFS.md`.
- If interrupted, resume from `RETRO.md` "Next" plus the current git branch.

## Amendments

`GOAL.md` may be amended when execution reality changes. Record meaningful changes in `RETRO.md`.

## Stop Rules

- A useful probe requires real Turso Cloud credentials or paid resources.
- A safe answer requires making Turso/libSQL the default backend.
- The working tree contains unrelated user changes that overlap with the storage files and cannot be safely separated.
- `just check` cannot be run or cannot pass for reasons unrelated to this branch.
