# Packet-staged launch - execution ledger

Durable execution ledger for packet-based Dispatch launch work.

## Execution Summary

- 2026-06-15: Packet created from Matt/Codex design discussion about Dispatch as
  a control plane for parallel Codex worker lanes.
- Planner did not implement source changes, create branches, create PRs, or
  update trackers.
- Starting repo state from `context-prime.sh`:
  - cwd: `/Users/mg/Developer/outfitter/dispatch`
  - branch: `main`
  - state: `main...origin/main`
  - recent head: `9150aab fix: enforce registry lane relationships (#44)`
  - open PRs for current branch: none

## Branch / PR / Issue Ledger

- Planning packet only.
- Branch: `main` at planning time.
- PR: none.
- Issues: none created or updated by planner.
- Executor should create a focused Graphite branch per phase if implementing
  under repo conventions, and should record exact branch/PR/issue state here.

## Key Decisions Captured

- Use `--input-file`, not `--text-file`.
- Support stdin for file-like inputs, with a one-stdin-consumer limit.
- Use packet conventions:
  - `dispatch.toml`
  - `goal.md`
  - `prompt.md`
  - `output.schema.json`
  - `base.md`
  - `developer.md`
  - `hooks/`
  - `codex/`
- Stage packet files into `.agents/sessions/<dispatch-ref>/packet/` inside the
  actual launched cwd.
- Use Dispatch short refs as session ids.
- Keep Dispatch generic; repo-local tooling owns packet generation and domain
  workflow.
- Dispatch stages hooks/config but does not execute arbitrary hooks.
- Hook trust bypass is allowed only as explicit operator/trusted policy, not as
  packet-only authority.
- Native Codex worktree support requires a protocol spike; do not assume paths.

## Execution Log

- Not started.

## Verification Log

- Planning verification:
  - Read repo `AGENTS.md` supplied in prompt.
  - Read `.agents/plans/PLANNING.md`.
  - Ran `/Users/mg/.agents/skills/goal-planning/scripts/context-prime.sh`.
  - Read goal-planning `code-review.md`, `source-control.md`, and
    `goal-runtimes.md`.
  - Inspected current `dispatch schema new`, current `NewInput`, `NewSettings`,
    `thread/start`, and `turn/start` schema facts during the design discussion.

## Discoveries

- Current Dispatch already has internal `output_schema` plumbing through
  `NewSettings` and `turn_start`.
- Current generated App Server schema includes `turn/start.outputSchema`.
- Current generated App Server schema shows `thread/start.config` as a raw
  object but Dispatch does not model it yet.
- Current generated App Server `UserInput` supports text, image URL, local
  image, skill, and mention inputs; packet v1 should start with text only unless
  images/mentions become a concrete requirement.

## Tracker Mutations

- None by planner.
- Executor should record any Linear/GitHub issue creation, comments, status
  changes, labels, dependencies, or explicit non-mutation decisions here.

## Local Review Log

- Not started.
- Executor must request local review before ready/handoff and record score,
  summary, findings, and P0/P1/P2 closeout here.

## Remote Review / CI Log

- Not started.
- Executor must record draft PR submission, CI/check state, remote code-review
  bot/agent summaries/scores, unresolved review-thread state, and any skipped
  remote review here.

## Forbidden Actions Audit

- Planner did not implement the target feature.
- Planner did not mutate live Dispatch daemon/user Codex state for this packet.
- Planner did not create a branch, commit, push, submit a PR, merge, publish, or
  change release state.
- Executor must preserve these constraints unless explicitly authorized.

## Final State

- Status: planning packet seeded, execution not started.
- Completion criteria for executor are in [`PLAN.md`](./PLAN.md) and
  [`GOAL.md`](./GOAL.md).
