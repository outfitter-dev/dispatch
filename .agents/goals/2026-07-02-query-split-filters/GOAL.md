# Goal: query-split-filters

Date: 2026-07-02
Status: Ready
Spec: `.agents/goals/2026-07-02-query-split-filters/SPEC.md`
Prompt: `.agents/goals/2026-07-02-query-split-filters/PROMPT.md`
Retro: `.agents/goals/2026-07-02-query-split-filters/RETRO.md`
Refs: `.agents/goals/2026-07-02-query-split-filters/REFS.md`

## Completion Horizon

Merged.

Complete when:

- `DIS-29`, `DIS-30`, `DIS-31`, `DIS-32`, and `DIS-33` are implemented, reviewed, merged, and reconciled in Linear.
- Local `main` is synced and clean.
- Focused checks, full repo checks, local reviews, and live CLI smoke checks prove the new grammar works.

Not complete when:

- Only a plan or draft PR exists.
- `dispatch query` is an alias or wrapper around `search --local`.
- `search --local` remains the documented canonical local path.
- Tool-call metadata still requires raw payload spelunking for common cases.
- P0/P1/P2 review findings remain open.

## Authority

- May commit: yes, scoped to this goal.
- May push: yes.
- May open PR: yes, one branch or a small stack.
- May mark ready: yes, after local checks/reviews pass and CI is green.
- May merge: yes, after review gates and CI are satisfied.
- May publish/release: no, unless Matt explicitly extends the horizon.
- Needs user approval for: changing storage backend defaults, adding paid/cloud services, indexing large raw result bodies by default, or changing the completion horizon.

## Boundary

- In scope: contracts, CLI/MCP projections, query/search/history handlers, registry query helpers, tests, docs, skills, Linear updates, and goal retro.
- Out of scope: semantic/vector search, Turso migration, multi-machine sync, remote/cloud query service, and release publishing.
- Do not touch: unrelated provider work, user-level Codex/Claude config, live thread data except read-only smoke queries, secrets, or unrelated docs.

## Topology

Milestone stack or one cohesive branch. Prefer a small stack if the diffs naturally separate:

1. `DIS-29`: command/op split.
2. `DIS-30` + `DIS-32`: structured query filters and shared semantics.
3. `DIS-31`: concrete tool-call metadata.
4. `DIS-33`: docs/skills/MCP guidance and final polish.

The executor may merge slices when that produces a clearer diff, but must keep commits coherent and reviewable.

## Steps

1. Split the surfaces (`DIS-29`)
   - Outcome: `dispatch query` is a first-class op; `dispatch search` is App Server-backed only.
   - Scope: input/output contracts, handler routing, CLI/MCP projection, schemas/help, tests.
   - Gate: `dispatch schema search` and `dispatch schema query` are distinct; `dispatch query sqlite --json` works locally; `search --local` is removed or fails clearly.

2. Add indexed query filters (`DIS-30`, `DIS-32`)
   - Outcome: query can filter by structural indexed data and shares overlapping semantics with history.
   - Scope: query helper/model, registry SQL, handler validation, item-level JSON output, tests.
   - Gate: text+tool, tool-only, file-only, thread-ref, repo/date, type/role, and no-query validation tests pass.

3. Promote concrete tool-call metadata (`DIS-31`)
   - Outcome: concrete MCP tool calls such as `linear.save_issue` are queryable without `history --raw`.
   - Scope: safe normalized metadata extraction, filters, result fields, fixtures/tests.
   - Gate: a fixture or live-safe smoke proves `dispatch query --tool linear.save_issue --json` can find concrete tool-call records.

4. Update operator guidance (`DIS-33`)
   - Outcome: docs and first-party skills teach `search`, `query`, `history`, and `sync` distinctly.
   - Scope: README/usage/design/ADR updates if invalidated, `skills/dispatch`, `skills/dm` if relevant, MCP docs.
   - Gate: no remaining canonical `search --local` guidance; examples include jq-friendly query usage.

5. Review, merge, reconcile
   - Outcome: merged branch/stack, clean synced `main`, Linear updated.
   - Scope: local review loops, CI/PR readiness, merge, final smoke, retro.
   - Gate: no unresolved P0/P1/P2; `just check` passes; final smoke commands recorded.

## Reviews

Run local review per milestone or meaningful slice. Use at least one standing reviewer for continuity and one fresh targeted reviewer before final merge. Fix P0/P1/P2 findings before moving upward. Fix cheap P3s; record accepted residual P3s in `RETRO.md`.

## Evidence Contract

- `RETRO.md` records issue states, branch/PR state, checks, local review summaries, final CLI smoke output, docs/skill updates, and deferred risks.
- Linear comments summarize what landed for `DIS-29` through `DIS-33`.
- Final chat reports PRs, checks, review state, final git state, and proof that `query` is separate from `search`.

## Verification

- `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders .agents/goals/2026-07-02-query-split-filters/PROMPT.md`
- `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor .agents/goals/2026-07-02-query-split-filters`
- Focused tests for registry query helpers, handler validation, CLI derivation, MCP projection, and docs/schema examples.
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src tests`
- `uv run pytest`
- `just check`
- Live-safe smoke after merge-ready implementation:
  - `uv run dispatch schema search --json` or equivalent schema check.
  - `uv run dispatch schema query --json`.
  - `uv run dispatch query --tool linear.save_issue --limit 5 --json`.
  - `uv run dispatch search sqlite --limit 5 --json`.

Prompt/goal alignment: before execution, confirm `PROMPT.md` carries the core sequence, authority, checks, review gates, stop rules, definition of done, not-done states, and persistence plan from this file.

## Next Move

- If a check fails: reproduce narrowly, fix the smallest cause, rerun focused checks before broad checks.
- If progress stalls: cut scope to the next issue-sized slice and preserve the remaining work in Linear/RETRO.
- If scope is unclear: choose the smaller no-drift path and record the decision; ask Matt only if the decision changes storage defaults, privacy posture, or completion horizon.

## Waiting State

- Waiting on: CI, bot review, or PR mergeability only.
- How to check: `gh pr view`, `gh pr checks`, `gt log`, `gt sync`, and Linear issue state.
- Heartbeat cadence: as needed during external waits; no routine noisy status.
- Continue when: checks/reviews are green or only accepted P3s remain.
- Stop when: credentials, paid services, storage-default changes, or user-only decisions are required.
- Last checked: not started.

## Persistence

Use `.agents/goals/2026-07-02-query-split-filters/RETRO.md` as the execution ledger and `.agents/goals/2026-07-02-query-split-filters/tmp/reviews/` for scratch review reports.

## Amendments

`GOAL.md` may be amended when execution reality changes. Record meaningful changes in `RETRO.md`.

## Stop Rules

- A change would index sensitive raw argument/result payloads by default without an explicit policy decision.
- A change would make Turso/libSQL or another backend the default.
- A required App Server behavior is missing or changed and cannot be safely shimmed.
- Linear/GitHub/Graphite authority is missing for required mutations.
- The goal cannot proceed without Matt choosing a product/API direction.
