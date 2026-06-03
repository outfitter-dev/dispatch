# dispatch v0 — retro (execution ledger)

Durable record of what happened, resumable without chat history. The executor updates this **before every handoff, ready-for-review, merge, or pause**, and the final transcript points here. Status: **in progress** — Phase 0 complete & locally reviewed (2026-06-03); Phase 1 next.

Stack base note: phase branches are stacked on top of the docs packet tip (`docs-v0-goal-packet`), not directly on `main`, because the packet PRs (#1/#2/#3) are draft and not yet merged (no-merge constraint). One linear Graphite stack: `main → fix-review-converged → docs-decision-adrs → docs-v0-goal-packet → chore/scaffold → …`.

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
| 0 Scaffold | `chore/scaffold` | complete (local review 4/5, 2026-06-03) | Installable namespace pkg + uv/ruff/mypy/pytest/just/lefthook/CI; CLI+daemon stubs render help. |
| 1 Client + spikes | `feat/client` | pending | |
| 2 Contracts + CLI | `feat/contracts-cli` | pending | |
| 3 Triggers | `feat/triggers` | pending | |
| 4 MCP | `feat/mcp` | pending | |
| 5 Daemon lifecycle | `feat/daemon-lifecycle` | pending | |

## Verification log
- _(per phase)_ record: `just check` result, examples-as-tests, integration-suite result, scopes, any skipped checks.
- **Phase 0 (2026-06-03):** `just check` GREEN — `ruff check` clean, `ruff format --check` clean, `mypy --strict` clean (5 source files), `pytest` 2 passed (`tests/test_smoke.py`). `uv run dispatch --help` and `uv run dispatchd --help` both exit 0. Examples-as-tests + integration: N/A (no contract/client layer yet). Scope note: `spikes/` excluded from the gate (preserved probes; promoted into `tests/` in Phase 1). CI workflow added (`.github/workflows/ci.yml`) but not yet exercised on a runner.

## Review log (local gate + remote)
- _(per phase)_ record: local review `score n/5`, P0/P1/P2/P3 findings + outcomes, fixes made after review, and — once PRs get remote review — code-review bot/agent scores, prose summaries, Prompt-To-Fix blocks, CI/unresolved-thread state. Distinguish external-service lag from real blockers.
- **Phase 0 — local review (subagent, 2026-06-03): 4/5.** No P0/P1. Two P2 (docs correctness), both fixed before advancing:
  - P2 `README.md:6` — dead link `docs/specs/` (nonexistent) → fixed to `docs/development/design.md` (+ ADRs link).
  - P2 `RETRO.md` — Phase 0 row still `pending` / logs empty → fixed (this update: execution row, verification entry, this review entry).
  - Noted residual (not a finding): `packages = ["src/outfitter"]` ships the whole `outfitter/` namespace tree — harmless now; revisit if other `outfitter.*` distributions are ever colocated. Gate satisfied (≥4/5, no open P0/P1/P2).

## Forbidden-action audit
- No merge, no non-draft submit, no package publish, no registry mutation without explicit user OK. No source-control writes by subagents. No writes to the user's `~/.codex` or live daemon in tests. _Confirm clean at each handoff._
- **Phase 0 (2026-06-03): clean.** No merges, no non-draft submits, no publishes, no tracker mutations. The reviewer subagent was read-only (no source-control writes). No tests touch `~/.codex` (none exist yet). One local config change: added `.claude/settings.local.json` (`worktree.bgIsolation: none`) and gitignored it — needed so the packet-mandated in-place Graphite stack workflow runs in this background session (worktrees break `gt` stacking); it is machine-local and not committed.

## Final state
- _pending._ On completion: summarize what shipped, remaining risks, unresolved P3s, open follow-ups, and archive readiness (move packet to `.agents/plans/archive/`).
