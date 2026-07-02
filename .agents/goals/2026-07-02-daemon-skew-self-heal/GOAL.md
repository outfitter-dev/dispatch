# Goal: daemon-skew-self-heal

Date: 2026-07-02
Status: Ready
Spec: `.agents/goals/2026-07-02-daemon-skew-self-heal/SPEC.md`
Prompt: `.agents/goals/2026-07-02-daemon-skew-self-heal/PROMPT.md`
Retro: `.agents/goals/2026-07-02-daemon-skew-self-heal/RETRO.md`
Refs: `.agents/goals/2026-07-02-daemon-skew-self-heal/REFS.md`

## Completion Horizon

Merged.

Complete when:

- `DIS-28` is implemented, tested, reviewed, merged to `main`, and reconciled in Linear.
- Local `main` is synced and clean.
- Live-safe smoke proves an idle daemon can serve the current CLI after restart.

Not complete when:

- The fix is only an error-message improvement.
- The CLI restarts busy daemons silently.
- The branch has an open PR, failing CI, or unresolved P0/P1/P2 findings.

## Authority

- May commit: yes, scoped to this goal.
- May push: yes.
- May open PR: yes.
- May mark ready: yes, after local checks and CI are green.
- May merge: yes, after review gates are clear.
- May publish/release: no.
- Needs user approval for: changing launchd policy, restarting busy daemons by default, changing storage defaults, or publishing.

## Boundary

- In scope: control metadata, CLI restart/retry, lifecycle command ergonomics, tests, docs, skill guidance, Linear updates.
- Out of scope: remote mesh, App Server internals, release publishing, broad daemon supervisor redesign.
- Do not touch: user-level Codex/Claude config, live thread contents beyond read-only smoke, secrets, unrelated roadmap docs.

## Topology

One cohesive branch: `dis-28-detect-and-explain-dispatch-daemonclient-version-skew-after`.

## Steps

1. Add daemon metadata and skew detection
   - Outcome: CLI can distinguish missing current-CLI op support from ordinary errors.
   - Scope: control protocol, CLI caller, tests.
   - Gate: unit tests prove metadata and missing-op classification.

2. Add guarded self-heal
   - Outcome: idle daemon restarts and retries once; busy daemon gives clear recovery.
   - Scope: lifecycle helpers, CLI behavior, daemon status/restart command if needed.
   - Gate: tests cover idle, busy, restart failure, and no infinite retry.

3. Update guidance and review
   - Outcome: docs/skill explain automatic and manual recovery.
   - Scope: usage docs, skill, goal retro, local review.
   - Gate: `just check`, live-safe smoke, no P0/P1/P2 review findings.

4. Merge and reconcile
   - Outcome: PR merged, branch cleaned, Linear updated.
   - Scope: PR, CI, Graphite/GitHub, Linear, final retro.
   - Gate: clean synced `main`.

## Reviews

Run a focused local review after implementation. Fix P0/P1/P2 findings before PR readiness; fix cheap P3s or record them.

## Evidence Contract

- `RETRO.md` records design choices, checks, local review, live smoke, PR/merge state, and remaining risk.
- PR body includes context, behavior, verification, and risk notes.
- Linear `DIS-28` is linked to the PR and marked Done only after merge.

## Verification

- `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders .agents/goals/2026-07-02-daemon-skew-self-heal/PROMPT.md`
- `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor .agents/goals/2026-07-02-daemon-skew-self-heal`
- Focused CLI/control/lifecycle tests.
- `just check`
- Live-safe smoke:
  - `uv run dispatch daemon status --json`
  - a current-op command after daemon restart.
- Prompt/goal alignment: prompt must carry horizon, authority, boundaries, loop, verification, stop rules, and final proof.

## Next Move

- If a check fails: reproduce narrowly, fix the smallest cause, rerun focused tests before broad checks.
- If progress stalls: preserve evidence in `RETRO.md`, cut to the next safe slice, and continue unless a stop rule fires.
- If scope is unclear: choose conservative no-restart behavior and document it.

## Waiting State

- Waiting on: CI, bot review, or mergeability only.
- How to check: `gh pr view`, `gh pr checks`, `gt log`, `gt sync`, Linear issue state.
- Heartbeat cadence: as needed during external waits; no noisy routine status.
- Continue when: checks/reviews are green or only accepted P3s remain.
- Stop when: user-only approval is required.
- Last checked: not started.

## Persistence

Use this goal packet as the resume surface. Review reports go under `.agents/goals/2026-07-02-daemon-skew-self-heal/tmp/reviews/`.

## Amendments

`GOAL.md` may be amended when execution reality changes. Record meaningful changes in `RETRO.md`.

## Stop Rules

- A change would restart busy daemons silently.
- A change would alter LaunchAgent/auto-start policy globally.
- A change would require release publishing.
- Safe idle detection cannot be implemented without broader daemon state work.
