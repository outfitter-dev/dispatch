# Execution Retro: Claude Control-Plane Research

Date started: 2026-07-15
Date finalized: pending
Status: Prepared for delegated execution
Spec: `.agents/goals/2026-07-15-claude-control-plane-research/SPEC.md`
Goal: `.agents/goals/2026-07-15-claude-control-plane-research/GOAL.md`
Prompt: `.agents/goals/2026-07-15-claude-control-plane-research/PROMPT.md`
Refs: `.agents/goals/2026-07-15-claude-control-plane-research/REFS.md`

## Summary

- Objective: verify Claude session-control semantics and produce an implementation-ready Dispatch provider plan.
- Completion horizon: `ready-pr`.
- Authority: isolated low-cost research, scoped docs/spikes/ADRs/tracker, commit/push/draft/ready PR; no production implementation, merge, release, publish, live settings mutation, private endpoint, credential access, or existing-session messaging.
- Current state: packet prepared on `docs/claude-control-plane-research`; execution not started.
- Known baseline: Dispatch 0.10.0 observes Claude but controls only Codex; Claude Code 2.1.210 exposes several candidate direct surfaces; zmx 0.6.0 raw send is fire-and-forget.

## Readiness

- Prompt checked: passed at 3,994/4,000 characters with no unresolved placeholders.
- Goal/prompt alignment: passed preparation review; authority, boundary, sequence, reviews, evidence, stop rules, and completion horizon agree.
- Review blockers: none known before execution.
- Verification blockers: none known; live Claude model access must be confirmed by the delegate.
- Tracker blockers: none; DIS-9 and DIS-49 exist, while implementation issue decomposition is an execution deliverable.
- Authority blockers: global settings changes, existing-session mutation, tool installation/upgrades, private endpoints, production implementation, merge, release, and publish require separate approval.
- Next action: run packet checks, commit preparation, then delegate the raw `PROMPT.md` body as the first message to the research agent.

## Preparation Log

```text
2026-07-15 - Packet preparation
- Verified: clean synchronized main at Dispatch 0.10.0; no open PRs.
- Verified: all public new/send/steer/context/interject paths still terminate in the Codex App Server client protocol.
- Verified: Claude Code 2.1.210 advertises background agents, Agent View JSON discovery, durable/resumable session selectors, realtime stream JSON, hook events, remote control, named sessions, settings, permission modes, and worktrees.
- Verified: zmx 0.6.0 supplies persistent PTY sessions and raw input but explicitly provides no send completion marker or exit status.
- Tracker: DIS-9 is the existing hooks/receipts research issue; DIS-48 is usage capture only; DIS-49 specifies execution-provider shorthands but excludes the Claude transport.
- Decision: use a research/decision goal with a ready-PR horizon and require a hook-confirmed cross-process walking skeleton before implementation planning is accepted.
- Validation: `check-goal-prompt --no-placeholders` and `goal-loop-doctor` passed; `git diff --check` passed.
- Mutation audit: created only this goal packet branch and packet files; no Claude session/config/tracker/remote mutation.
```

## Goal Amendments

| Time | Change | Reason | Approved By |
| --- | --- | --- | --- |
| 2026-07-15 | Initial research-to-decision contract | User requested exhaustive research before Claude implementation planning | Matt |

## Execution Log

Execution has not started. Append chronological milestone, experiment, cleanup, tracker, and source-control evidence here.

## Experiment Ledger

| ID | Question | Method | Version/source | Result | Confidence | Artifact | Cleanup |
| --- | --- | --- | --- | --- | --- | --- | --- |
| prep-1 | Does Dispatch currently control Claude sessions? | Trace public handlers and client protocol | Dispatch 0.10.0 | No; control path is Codex-only | high | SPEC/REFS | n/a |
| prep-2 | Is zmx send a delivery receipt? | Inspect installed primary help | zmx 0.6.0 | No; raw input is fire-and-forget | high | REFS | n/a |

## Capability Matrix

The delegate must move every row to `verified`, `unsupported`, `product-decision`, or `blocked` and add evidence, confidence, failure/recovery, and implementation consequence.

| Operation/capability | Status | Claude primitive/composition | Acceptance/completion evidence | Confidence | Next experiment |
| --- | --- | --- | --- | --- | --- |
| durable identity | pending | | | | |
| new | pending | | | | |
| attach/resume | pending | | | | |
| send | pending | | | | |
| steer during active turn | pending | | | | |
| durable queue/readiness | pending | | | | |
| interject | pending | | | | |
| context injection | pending | | | | |
| stop/interrupt | pending | | | | |
| tail/history | pending | | | | |
| watch/events | pending | | | | |
| rename | pending | | | | |
| archive/restore | pending | | | | |
| goal loop | pending | | | | |
| permissions/approval | pending | | | | |
| user input/elicitation | pending | | | | |
| structured output | pending | | | | |
| rich input/files/images | pending | | | | |
| process restart/recovery | pending | | | | |
| duplicate/concurrent send | pending | | | | |
| remote/mesh compatibility | pending | | | | |

## Review Log

| Round | Scope | Report | Score | State | Open P0-P2 | Notes |
| --- | --- | --- | --- | --- | --- | --- |

## Verification Log

| Check | Scope | Result | Notes |
| --- | --- | --- | --- |

## Prompt / Goal Alignment

- Preparation review passed on 2026-07-15.
- `PROMPT.md` delegates the same authority, boundaries, evidence contract, review gates, stop rules, and ready-PR horizon defined by `GOAL.md`.

## Tracker / PR Log

| Item | State | Notes |
| --- | --- | --- |
| DIS-9 | Todo | Existing Claude hooks/provider-events research issue |
| DIS-48 | Todo | Adjacent usage-capture lifecycle; not messaging transport |
| DIS-49 | Todo | Provider selector and CLI shorthands; transport out of scope |

## Final State

- Completion proof: pending execution.
- Walking-skeleton proof: pending.
- Capability verdict: pending.
- Implementation plan: pending.
- Review summary: pending.
- Verification summary: pending.
- Cleanup audit: pending.
- Remaining product decisions/blockers: pending.
- Recommended first implementation slice: pending.
