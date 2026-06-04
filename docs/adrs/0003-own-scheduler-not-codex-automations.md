---
id: 0003
slug: own-scheduler-not-codex-automations
title: Own Scheduler, Not Codex Automations
status: accepted
created: 2026-06-02
updated: 2026-06-02
owners: ['Dispatch maintainers']
---

# ADR-0003: Own Scheduler, Not Codex Automations

## Context

dispatch automates pings to lanes on time and event triggers. Codex already has an automations system (cron + heartbeat) — but it is **filesystem/daemon-managed, not in the App Server protocol**: automations are TOML at `~/.codex/automations/<id>/automation.toml`, and a written file is not live-registered by the running daemon without its own rescan/registration path (verified: a written probe was untouched). Reusing it would couple us to an undocumented, daemon-internal registration mechanism and limit control over guards and event triggers.

## Decision

dispatch owns its scheduler. A small asyncio scheduler handles time triggers; a reactor consumes the App Server event stream for event triggers (`idle_for`, `turn_completed`, `waiting_on_approval`).

Because we own the scheduler we also own the trigger time-format: v1 supports **interval and cron only**, via `croniter` for cron fields. We deliberately do **not** support iCal RRULE (and so do not pull in `dateutil.rrulestr`) — Codex's automation RRULE format is irrelevant since we don't consume its TOML. RRULE is a later add only if a concrete need appears. Triggers are stored in dispatch's own registry, fire actions through the verified messaging primitives (`turn/start`, `turn/steer`, `inject_items`), and run through a guard layer (`idle_only`, `min_interval`, `dedupe`) that is also the seam for future conditional triggers. We do not write Codex automation TOML.

## Consequences

### Positive

- Full control over trigger semantics, guards, and event reactions; no dependence on a daemon-internal black box.
- Reliable signals: we read `turn/completed` / `waitingOnApproval` directly instead of relying on a delegate remembering to ping.

### Tradeoffs

- We own scheduling correctness and persistence (use an injectable clock for deterministic tests).
- Two schedulers exist on the machine (Codex's and ours); they are independent by design.

## Alternatives considered

- **Write Codex automation TOML** — live registration is daemon-internal/unconfirmed; weak control.
- **APScheduler** — heavier and more magic than our handful of trigger types need.

## References

- `docs/development/design.md`; `docs/research/orchestration-thesis.md` (automations are filesystem/daemon, not protocol)
