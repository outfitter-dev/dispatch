---
id: 0005
slug: lane-authority-capability-ladder
title: Lane Authority Capability Ladder
status: accepted
created: 2026-06-02
updated: 2026-06-05
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0005: Lane Authority Capability Ladder

> Accepted (2026-06-03) after the Phase-1 cross-process spike. The spike did **not** clear attached-lane turn writes — it confirmed the write-locked default and revealed that cross-process observation is not live. See "Phase-1 spike outcome" below. The gated turn-write rungs remain locked for v0.

## Context

dispatch drives both lanes it **owns** (created via `open`) and lanes it **attaches** to (existing desktop threads registered from App Server metadata). Owned lanes have no other writer. Attached lanes are also live in the desktop Codex app — a separate app-server process over the same `~/.codex` store. We verified that a second connection resuming a *persisted* thread receives live event fan-out, but we did **not** verify that two app-servers running turns on the same thread concurrently is safe. Critically, dispatch's planned advisory lock is **dispatch-local**: it cannot stop the desktop app, which knows nothing about it.

## Decision

Authority over a lane is a ladder, not a flag:

- **Owned lanes** (dispatch created them): full read/write, always.
- **Attached lanes** (existing desktop threads): turn-writing and history-mutating ops are blocked by default — read metadata, sync a local index, read history, and allow explicit metadata/lifecycle actions (`rename`, `archive`, `restore`); no `send`/`steer`/`brief`/`interrupt`, `goal set/clear`, `fork`, `rollback`, or `compact`.
- **idle-only-write** and **full-write** on attached lanes are unlocked only when **(a)** the slice-0 cross-process spike shows it is safe, **and (b)** the user explicitly opts in (per-lane or global).

## Assumptions (must hold; verify before relying)

1. Owned lanes truly have no concurrent external writer.
2. Merely *observing* an attached lane (metadata reads, history reads, and explicit sync) is safe alongside the desktop app — to be confirmed by the spike, not assumed.
3. The spike can actually distinguish "safe" from "racy" for concurrent turns on a shared thread.

## Consequences

- Honors the user's "full read/write" intent where it is safe today (owned lanes) and stays honest where it is not (attached lanes), instead of shipping an unverified guarantee.
- The advisory lock is treated as intra-dispatch coordination only — never as cross-process safety.

## Phase-1 spike outcome (2026-06-03)

Two `codex app-server` processes shared one isolated `CODEX_HOME` (modelling our daemon vs the desktop app). Driven through the typed client:

- **Discovery works cross-process:** process B sees A's persisted thread via `thread/list(useStateDbOnly:true)`.
- **Resume works cross-process:** B can `thread/resume` A's persisted thread and read its history.
- **Live fan-out does NOT cross processes:** while A ran a turn, B (resumed) received **zero** live events. Live event fan-out is intra-process only (one app-server process). The spike-04 "resume = live co-presence" finding holds only for multiple connections to the *same* server process — which is exactly dispatch's own topology (ADR-0002), not the desktop-vs-daemon case.
- **Concurrent turns are uncoordinated:** A and B each ran a turn on the shared thread with no error returned, but there is no cross-process interlock (dispatch's advisory lock is dispatch-local and cannot gate the desktop app), so "no error" is not "safe."

**Decision:** keep attached lanes locked for turn-writing and history-mutating ops in v0. Observation is limited to metadata reads, explicit sync, history read, and periodic re-read (no live cross-process stream). ADR-0018 permits explicit metadata/lifecycle actions (`rename`, `archive`, `restore`) because they do not start turns, steer turns, or mutate turn history. ADR-0017 makes default attach metadata-only instead of `thread/resume`-based. The idle-only-write and full-write rungs stay locked; unlocking them needs a real cross-process interlock, which Codex does not expose today. This is the safe default the ladder already proposed — the spike confirms rather than relaxes it.

## Alternatives considered

- **Full read/write on attached lanes now** — rejected: the spike shows cross-process turns are uncoordinated and the advisory lock can't gate the desktop app.
- **Own-lanes-only for v1** — rejected: loses the attach-to-existing value the user wants; the ladder keeps it as a gated opt-in instead.

## References

- ADR-0002 (Single Daemon over One App Server); ADR-0017 (Progressive Thread Sync Index); ADR-0018 (Top-Level Thread Actions and Search); `docs/research/app-server-verification.md` (resume fan-out, cross-process untested); `PLAN.md` Phase-1 slice-0 spike.
