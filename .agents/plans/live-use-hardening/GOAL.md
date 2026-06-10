# Live-use hardening - pasteable goal

```text
/goal Work in the dispatch repo root. Implement the live-use hardening plan in .agents/plans/live-use-hardening/PLAN.md.

Context: a real Trails delegation attempt exposed trust failures that unit/parity tests did not catch. Dispatch accepted work that had not proven alive, hid model/system failures behind raw watch events, let slash-command goal text look like a native goal, left daemon lifecycle commands outside the JSON/scriptability contract, and allowed CLI projection hand-wiring to grow beyond the no-drift doctrine.

Objective: make dispatch trustworthy for live agent coordination. Document the incident, add regression tests and guardrails, tighten derived surface boundaries, fix launch/error/status semantics, make cleanup/lifecycle commands agent-safe, update docs/skills, and run local review until no P0/P1/P2 issues remain.

Required outcomes:
- A durable plan/retro records decisions, checks, review findings, and deferred work.
- Public CLI/MCP projections are governed by explicit projection metadata or an allowlisted control-surface contract; ungoverned hand-wired per-op routes are test failures.
- `new`/`send` outputs distinguish accepted delivery from proof of execution.
- `get`/list-like status surfaces expose latest turn/error state well enough that raw `watch` is not required to discover obvious model/system failures.
- `/goal ...` as message text is either rejected/warned or replaced by a first-class `new --goal` path that calls the native goal API.
- Destroy operations have explicit non-interactive confirmation support, and `up`/`down` expose JSON output.
- Registry schema recovery is boring: doctor/up explain or expose a safe migrate/repair path without manual DB surgery.
- Docs, README, skills, plugin docs, schemas/help, tests, and ADR/rules are updated where behavior or doctrine changes.
- Checks pass, including focused tests and `just check`; run local review loops and fix P2+ findings.

Constraints:
- Preserve contract-first/no-drift architecture; if a surface needs special ergonomics, make the override explicit and tested.
- Do not touch live user Codex state in tests. Use isolated `DISPATCH_HOME`/`CODEX_HOME` for any smoke.
- Do not merge, publish, or mutate release state unless explicitly asked.
- If model preflight cannot be made reliable from the current App Server contract, surface the first failure clearly and record the limitation.

Done only when all required outcomes are implemented or explicitly deferred with evidence, local checks pass, review P2+ is clear, and RETRO.md contains final proof.
```
