# dispatch v0 — goal

Long-running execution goal for the dispatch v0 build. The pasteable `/goal` below is the evaluator contract: it keeps the loop alive without chat history. Detail lives in `PLAN.md` (phases, gates, verification), `design.md` (architecture), `docs/adrs/` (decisions), `RETRO.md` (ledger). Runtime notes: works as a Codex or Claude `/goal`; the executor owns the Graphite stack and per-phase local-review gate.

## Pasteable `/goal`

```text
/goal Implement dispatch v0. cwd: ~/Developer/outfitter/dispatch. Packet: .agents/plans/v0/ — read PLAN.md (phases + gates + review contract) and RETRO.md (where execution left off) before acting; design.md = architecture; docs/adrs/0000-0009 = decisions; .agents/plans/PLANNING.md = conventions.

OBJECTIVE: build dispatch v0 end-to-end as a Graphite stack, one branch per PLAN phase (0 scaffold → 1 client+spikes → 2 contracts+CLI → 3 triggers → 4 MCP → 5 daemon-lifecycle), each green and LOCALLY REVIEWED before the next begins.

LOOP, per phase N: (1) implement TDD on the phase branch per PLAN; (2) run the validation ladder until green; (3) self-review; (4) request a LOCAL review in the code-review contract (Overall score n/5; P0-P3 findings with file:line + Prompt-To-Fix); (5) fix P0-P2 (P3 fix-if-cheap else record deferred); (6) update RETRO.md with the round; (7) gt submit --draft; (8) advance to N+1 ONLY after the gate passes — local reviewer >=4/5 with no open P0/P1/P2, or explicit user OK. Post a short progress note each phase: phase, branch, ladder result, review score, next.

VALIDATION LADDER: just check (ruff + ruff format --check + mypy --strict + pytest) -> examples-as-tests (test_examples) -> integration vs a REAL ephemeral codex app-server (isolated CODEX_HOME, ephemeral:true lanes). Phase 1 has two gating spikes — cross-process safety (gates ADR-0005 attached-lane authority) and concurrent-lane backpressure (F); record findings in RETRO.md and STOP for a decision if either is unsafe.

COMPLETION (all true): phases 0-5 done; PLAN Definition of Done met (open/attach lanes [owned read/write; attached observe-only unless ADR-0005 cleared], send/steer/brief/interrupt, time+event triggers, a CLI AND an MCP server derived from ONE op registry with a passing parity test, a recoverable daemon); CI green per branch; each phase locally reviewed; RETRO.md finalized (execution, verification, review, forbidden-action logs + final state). The final transcript points to RETRO.md's final state — a transcript-only report is NOT sufficient.

CONSTRAINTS: honor ADRs 0000-0009; one contract set -> derived surfaces (never hand-write a CLI command or MCP tool); typed exceptions projected at the surface boundary; pin the codex binary and drive it directly (not the openai-codex SDK); NEVER touch the user's ~/.codex or live daemon in tests; subagents perform no source-control writes; PRs stay DRAFT — no merge, no non-draft submit, no package publish without explicit user OK.

STOP/PAUSE and report if: a Phase-1 spike shows cross-process unsafe (keep attached lanes observe-only) or backpressure problematic; a gate cannot pass after reasonable attempts; blocked on Graphite sync; any P0 needs a human decision; or scope materially changes.

Before any handoff / ready-for-review / merge: update RETRO.md, then summarize its final state in the report.
```

## Notes for the launcher
- Prerequisite: the review-findings stack (PRs #1/#2) and this packet should be on `main` (or the stack base) first, and the repo synced to Graphite so `gt submit` works. Until synced, open PRs via `gh` (draft).
- The local-review gate may be satisfied by a reviewer subagent, a Codex review, or the user — record whichever in `RETRO.md`.
