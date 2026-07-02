# Goal Execution Contract: local-substrate-stack

Date: 2026-07-02
Status: Active
Spec: `.agents/goals/2026-07-02-local-substrate-stack/SPEC.md`
Prompt: `.agents/goals/2026-07-02-local-substrate-stack/PROMPT.md`
Retro: `.agents/goals/2026-07-02-local-substrate-stack/RETRO.md`
Refs: `.agents/goals/2026-07-02-local-substrate-stack/REFS.md`

## Completion Horizon

merged

Complete when:

- Roadmap/tracker setup is merged.
- All feasible milestones are attempted in order with review gates.
- Completed milestones are merged; deferred milestones have Linear issues and retro notes.
- Local `main` is synced clean.

Not complete when:

- The roadmap is only in chat.
- Linear issues are missing or stale.
- A milestone moves forward with unresolved P0/P1/P2 findings.
- A Turso default, real cloud credential, live user data, or real embedding action is required.

## Authority

- May commit: yes, scoped to this goal.
- May push: yes.
- May open PR: yes.
- May mark ready: yes after checks and reviews.
- May merge: yes when CI/review gates are clean.
- May publish/release: no.
- Needs user approval for: default backend changes, live data migration, cloud credentials, paid embedding/API calls, gateway runtime implementation, releasing/publishing, or weakening review gates.

## Boundary

- In scope: docs, ADR/research notes, goal packet, Linear updates, small storage/test/harness code, synthetic fixtures, PRs.
- Out of scope: production Turso default, real gateway runtime, real remote sync, live user data, real embeddings, secrets.
- Do not touch: user global Codex/Claude config, real `~/.codex`, unrelated branches, unrelated repos.

## Topology

Milestone stack with local reviews after each milestone. Keep one branch if the work stays docs-first; split branches if code milestones become independently reviewable.

## Steps

1. Roadmap and gateway boundary
   - Outcome: `DIS-20` through `DIS-25` exist, roadmap note exists, gateway doc says route intent not logs.
   - Scope: docs and tracker.
   - Gate: docs checks plus local review; merge if clean.

2. Storage boundary first slice
   - Outcome: smallest useful connection/transaction or contract-test boundary is implemented or precisely deferred.
   - Scope: registry store/tests/spike harness.
   - Gate: focused registry tests, Turso/libSQL probe if relevant, `just check`, local review.

3. Event ingestion harness
   - Outcome: synthetic opt-in load harness or documented blocker.
   - Scope: test/scenario/spike harness and docs.
   - Gate: focused harness run, `just check` if product code changes, local review.

4. Semantic search policy/prototype
   - Outcome: derived-artifact indexing policy and optional fake-data prototype.
   - Scope: docs/tests/synthetic fixtures only unless small code is justified.
   - Gate: privacy-focused review and focused checks.

5. Multi-machine selected-state sync design
   - Outcome: ADR/doc update reconciles mesh, queues, selected sync, and gateway boundary.
   - Scope: docs/ADRs/Linear follow-ups.
   - Gate: architecture review and docs checks.

## Reviews

- After each milestone, run local-review style review and record report path/state in `RETRO.md`.
- Fix all P0/P1/P2 before moving on.
- Fix easy useful P3s; otherwise record accepted/deferred P3s.

## Evidence Contract

- Linear issue IDs and links.
- Changed files and PRs by milestone.
- Exact commands and results.
- Review report paths, scores, and open P0/P1/P2 counts.
- Explicit deferred scope and reasons.
- Final branch/PR/main sync proof.

## Verification

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run python -m pytest tests/registry -q` after storage changes.
- `uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py` after storage SQL/probe changes.
- `just check` before ready/merge for any product code or broad docs stack.
- Goal packet: `check-goal-prompt --no-placeholders` and `goal-loop-doctor`.

## Next Move

- If checks fail: isolate, fix, rerun focused checks, then broad checks.
- If scope sprawls: stop the current milestone, record deferral, and move to the next safe milestone.
- If a milestone needs forbidden authority: defer it with Linear notes.

## Waiting State

- Waiting on: CI, PR review, or package install only.
- How to check: `gh pr checks`, `gh pr view`, `gt log --no-interactive`.
- Heartbeat cadence: none unless CI/review waits exceed 20 minutes.
- Continue when: checks/reviews are green.
- Stop when: a stop rule fires.

## Persistence

- Keep `RETRO.md` current after every milestone.
- Resume from `RETRO.md`, current branch, and Linear `DIS-20` children.

## Stop Rules

- Required work needs real user data, secrets, cloud credentials, paid APIs, or default backend migration.
- `just check` fails for unrelated reasons that cannot be isolated.
- Unrelated user changes overlap the same files and cannot be safely separated.
