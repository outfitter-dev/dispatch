# dispatch v0 — retro (execution ledger)

Durable record of what happened, resumable without chat history. The executor updates this **before every handoff, ready-for-review, merge, or pause**, and the final transcript points here. Status: **not started** (packet authored 2026-06-02).

## How to resume
Read this file top-to-bottom for where execution left off, then `gt log` for stack state and `gh pr list` for PR state. Continue the next un-passed phase gate in `PLAN.md`.

## Discoveries / decisions during execution
- _(none yet)_ — record design surprises, spike findings, and any deviation from PLAN/ADRs here. New decisions worth keeping become ADRs.

## Spike findings (Phase 1, gating)
- **Cross-process safety (gates ADR-0005):** _pending._ Record: is concurrent turn execution by our app-server + the desktop app on one shared thread safe? Does observe-only attach work cleanly? Outcome → attached-lane authority.
- **Concurrent-lane backpressure (F):** _pending._ Record: head-of-line behavior with N concurrent active turns on one stdio stream.

## Tracker mutations
- _(none yet)_ — no external tracker wired for dispatch yet; note if one is added.

## Execution log (per phase)
| Phase | Branch | Status | Notes |
| --- | --- | --- | --- |
| 0 Scaffold | `chore/scaffold` | pending | |
| 1 Client + spikes | `feat/client` | pending | |
| 2 Contracts + CLI | `feat/contracts-cli` | pending | |
| 3 Triggers | `feat/triggers` | pending | |
| 4 MCP | `feat/mcp` | pending | |
| 5 Daemon lifecycle | `feat/daemon-lifecycle` | pending | |

## Verification log
- _(per phase)_ record: `just check` result, examples-as-tests, integration-suite result, scopes, any skipped checks.

## Review log (local gate + remote)
- _(per phase)_ record: local review `score n/5`, P0/P1/P2/P3 findings + outcomes, fixes made after review, and — once PRs get remote review — code-review bot/agent scores, prose summaries, Prompt-To-Fix blocks, CI/unresolved-thread state. Distinguish external-service lag from real blockers.

## Forbidden-action audit
- No merge, no non-draft submit, no package publish, no registry mutation without explicit user OK. No source-control writes by subagents. No writes to the user's `~/.codex` or live daemon in tests. _Confirm clean at each handoff._

## Final state
- _pending._ On completion: summarize what shipped, remaining risks, unresolved P3s, open follow-ups, and archive readiness (move packet to `.agents/plans/archive/`).
