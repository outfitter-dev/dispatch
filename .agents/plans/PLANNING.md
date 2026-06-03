# Planning conventions (dispatch)

How goal packets and phased execution work in this repo. Read before planning or executing a goal. (Authority: repo `AGENTS.md` > this file > tool defaults.)

## Packet layout
- Active packets: `.agents/plans/{slug}/` with `PLAN.md` (phases + gates), `GOAL.md` (pasteable `/goal` + loop), `RETRO.md` (durable ledger), `REFS.md` (pointers). The current packet is `v0/`.
- `.agents/plans` is tracked; `.agents/notes/` is local-only (gitignored) for scratch/session notes.
- On completion, move the whole packet to `.agents/plans/archive/`.

## Phased execution = Graphite stack
- One phase = one Graphite branch, stacked in order on `main`. PRs stay **draft** until their review gate passes. `gt submit --stack --draft` once the repo is synced to Graphite (else open draft PRs via `gh`).
- Phases must be small and independently reviewable.

## Verification ladder (every phase, green before review)
1. `just check` = `ruff check` + `ruff format --check` + `mypy --strict` + `pytest`
2. examples-as-tests (`test_examples(registry)`) once the contract layer exists
3. integration vs a **real ephemeral `codex app-server`** (isolated `CODEX_HOME`, `ephemeral:true` lanes) — never the user's `~/.codex` or live daemon

## Local review gate (between phases)
After a phase is green + self-reviewed, request a **local review** before starting the next phase. Output contract:
```
Overall score: n/5
Summary: <one line>
Findings:
- P0|P1|P2|P3 — <file:line> — <finding>
  Prompt To Fix With AI: <concise fix prompt>
No-findings statement: <inspected, residual risk>
```
Severity: P0 cannot-proceed · P1 correctness/contract regression · P2 quality/docs/coverage (docs correctness is P2) · P3 style. Fix P0–P2; P3 fix-if-cheap or record deferred. Advance only at ≥4/5 with no open P0/P1/P2, or explicit user OK. Record every round in `RETRO.md`.

## Retro discipline
`RETRO.md` is the source of truth for what happened. Update it before every handoff/ready/merge/pause; keep execution, verification, review, and forbidden-action logs current; finalize it before claiming completion. A transcript-only report is never sufficient.

## Constraints
Honor the repo's ADRs (`docs/adrs/`). Subagents do no source-control writes. No merge / non-draft submit / publish without explicit user OK.
