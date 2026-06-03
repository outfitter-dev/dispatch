# dispatch v0 — retro (execution ledger)

Durable record of what happened, resumable without chat history. The executor updates this **before every handoff, ready-for-review, merge, or pause**, and the final transcript points here. Status: **in progress** — Phase 0 complete & locally reviewed (2026-06-03); Phase 1 next.

Stack base note: phase branches are stacked on top of the docs packet tip (`docs-v0-goal-packet`), not directly on `main`, because the packet PRs (#1/#2/#3) are draft and not yet merged (no-merge constraint). One linear Graphite stack: `main → fix-review-converged → docs-decision-adrs → docs-v0-goal-packet → chore/scaffold → …`.

## How to resume
Read this file top-to-bottom for where execution left off, then `gt log` for stack state and `gh pr list` for PR state. Continue the next un-passed phase gate in `PLAN.md`.

## Discoveries / decisions during execution
- _(none yet)_ — record design surprises, spike findings, and any deviation from PLAN/ADRs here. New decisions worth keeping become ADRs.

## Spike findings (Phase 1, gating)
- **Cross-process safety (gates ADR-0005): RESOLVED (2026-06-03).** Two app-server processes sharing one isolated `CODEX_HOME` (= our daemon vs the desktop app), driven through the typed client. Cross-process **discovery** (`thread/list useStateDbOnly`) and **resume** (history read) both work. Cross-process **live fan-out does NOT happen** — process B received 0 live events while process A ran a turn; live fan-out is intra-process only (spike-04's "resume = live co-presence" was same-server multi-connection, which is dispatch's *own* topology, ADR-0002). Concurrent turns by both processes returned no error but are uncoordinated (advisory lock is dispatch-local; can't gate the desktop). **Outcome:** attached lanes stay **observe-only** for v0 (resume + history read; no live stream; writes locked). ADR-0005 → **Accepted** (the spike confirms the safe default rather than clearing the write rungs). **No scope change** — the plan already defaulted attached=observe-only. Evidence: `.agents/notes/phase1-spikes.md`, `tmp/spike_xproc_backpressure.py`.
- **Concurrent-lane backpressure (F): RESOLVED (2026-06-03).** One server, 3 lanes, concurrent turns on one stdio stream: all three `turn/started` at t≈0 and events **interleave** (3/3 lanes active in the first half of the event log) — **no head-of-line blocking**. One stdio connection multiplexes N concurrent active lanes fine. Risk F is **LOW**. No scope change.
- **Schema discovery:** `thread.status` is an OBJECT (`{"type":"idle"}`), not a string — fixed in `client/models.py` (`ThreadStatus`); caught by integration, not unit. A non-ephemeral thread persists (listable/archivable/resumable) only **after** a turn completes; before that, those ops error `no rollout found`.

## Tracker mutations
- _(none yet)_ — no external tracker wired for dispatch yet; note if one is added.

## Execution log (per phase)
| Phase | Branch | Status | Notes |
| --- | --- | --- | --- |
| 0 Scaffold | `chore/scaffold` | complete (local review 4/5, 2026-06-03) | Installable namespace pkg + uv/ruff/mypy/pytest/just/lefthook/CI; CLI+daemon stubs render help. |
| 1 Client + spikes | `feat/client` | complete (local review 4/5, 2026-06-03) | Typed async client (transport/router/client/events/models/errors); LaneEvent projection (ADR-0007); 26 unit + 5 integration tests green; both gating spikes resolved (see above); close()/stderr-task review fixes applied. |
| 2 Contracts + CLI | `feat/contracts-cli` | pending | |
| 3 Triggers | `feat/triggers` | pending | |
| 4 MCP | `feat/mcp` | pending | |
| 5 Daemon lifecycle | `feat/daemon-lifecycle` | pending | |

## Verification log
- _(per phase)_ record: `just check` result, examples-as-tests, integration-suite result, scopes, any skipped checks.
- **Phase 0 (2026-06-03):** `just check` GREEN — `ruff check` clean, `ruff format --check` clean, `mypy --strict` clean (5 source files), `pytest` 2 passed (`tests/test_smoke.py`). `uv run dispatch --help` and `uv run dispatchd --help` both exit 0. Examples-as-tests + integration: N/A (no contract/client layer yet). Scope note: `spikes/` excluded from the gate (preserved probes; promoted into `tests/` in Phase 1). CI workflow added (`.github/workflows/ci.yml`) but not yet exercised on a runner.
- **Phase 1 (2026-06-03):** Unit gate GREEN — `just check`: ruff clean, ruff format clean, `mypy --strict` clean (23 source files), `pytest` **26 passed**, 5 integration deselected (unit gate stays fast/CI-safe; integration deselected by `-m 'not integration'`). Integration ladder GREEN — `just test-int`: **5 passed in 33s** against a real ephemeral `codex app-server` (isolated `CODEX_HOME`, auth copied read-only, ephemeral/archived lanes): read-only turn → `pong`; `inject_items` recall (BANANA); `thread/list` reads `result.data`; approval-accept resumes a workspace-write turn (file written on disk); same-connection persisted-resume yields live events. Gotchas encoded in models/tests: `thread/start.sandbox` string vs `turn/start.sandboxPolicy` object; `turn/steer` requires `expectedTurnId`; file-change approval carries no diff (correlate by `itemId`); `thread.status` is an object. Forbidden-action audit: integration used a temp `CODEX_HOME` only — the user's `~/.codex` was never set as CODEX_HOME (auth.json copied read-only, never mutated).

## Review log (local gate + remote)
- _(per phase)_ record: local review `score n/5`, P0/P1/P2/P3 findings + outcomes, fixes made after review, and — once PRs get remote review — code-review bot/agent scores, prose summaries, Prompt-To-Fix blocks, CI/unresolved-thread state. Distinguish external-service lag from real blockers.
- **Phase 0 — local review (subagent, 2026-06-03): 4/5.** No P0/P1. Two P2 (docs correctness), both fixed before advancing:
  - P2 `README.md:6` — dead link `docs/specs/` (nonexistent) → fixed to `docs/development/design.md` (+ ADRs link).
  - P2 `RETRO.md` — Phase 0 row still `pending` / logs empty → fixed (this update: execution row, verification entry, this review entry).
  - Noted residual (not a finding): `packages = ["src/outfitter"]` ships the whole `outfitter/` namespace tree — harmless now; revisit if other `outfitter.*` distributions are ever colocated. Gate satisfied (≥4/5, no open P0/P1/P2).
- **Phase 1 — local review (subagent, 2026-06-03): 4/5.** No P0. One P1 + one P2, both fixed before advancing:
  - P1 `client/client.py` — `close()` could orphan in-flight request futures if cancellation beat stdout EOF (deadlock). Fixed: `close()` now calls `router.fail_all(TransportError("client closed"))` up front and awaits the cancelled read task (`contextlib.suppress(CancelledError)`).
  - P2 `client/transport.py` — `close()` cancelled the stderr-drain task without awaiting it ("pending task" warnings). Fixed: await with `contextlib.suppress(CancelledError)`.
  - Re-verified: unit gate green (26 passed); one real-server integration test (pong) green in 5.7s with clean teardown. Residual (reviewer-noted, not a finding): unbounded broadcaster queues under a slow consumer — acceptable at this scale; revisit if a surface stalls. Gate satisfied (≥4/5, no open P0/P1/P2).

## Forbidden-action audit
- No merge, no non-draft submit, no package publish, no registry mutation without explicit user OK. No source-control writes by subagents. No writes to the user's `~/.codex` or live daemon in tests. _Confirm clean at each handoff._
- **Phase 0 (2026-06-03): clean.** No merges, no non-draft submits, no publishes, no tracker mutations. The reviewer subagent was read-only (no source-control writes). No tests touch `~/.codex` (none exist yet). One local config change: added `.claude/settings.local.json` (`worktree.bgIsolation: none`) and gitignored it — needed so the packet-mandated in-place Graphite stack workflow runs in this background session (worktrees break `gt` stacking); it is machine-local and not committed.
- **Phase 1 (2026-06-03): clean.** No merges, no non-draft submits, no publishes, no tracker mutations. Reviewer subagent read-only. Integration + spikes ran a real `codex app-server` ONLY under a temp `CODEX_HOME` (auth.json copied read-only; the user's `~/.codex` was never set as CODEX_HOME and never written). Real model turns were run under the user's account (inherent to "integration against a real app-server"), minimized via `effort:"low"` and ephemeral/archived lanes. Spike scripts live in the job tmp dir (outside the repo); only typed integration tests are committed.

## Final state
- _pending._ On completion: summarize what shipped, remaining risks, unresolved P3s, open follow-ups, and archive readiness (move packet to `.agents/plans/archive/`).
