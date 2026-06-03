---
id: 0005
slug: lane-authority-capability-ladder
title: Lane Authority Capability Ladder
status: proposed
created: 2026-06-02
updated: 2026-06-02
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0005: Lane Authority Capability Ladder

> Proposed — gated on the Phase-1 slice-0 cross-process spike. Assumptions are called out below; do not treat full read/write on attached lanes as settled.

## Context

dispatch drives both lanes it **owns** (created via `open`) and lanes it **attaches** to (existing desktop threads via `resume`). Owned lanes have no other writer. Attached lanes are also live in the desktop Codex app — a separate app-server process over the same `~/.codex` store. We verified that a second connection resuming a *persisted* thread receives live event fan-out, but we did **not** verify that two app-servers running turns on the same thread concurrently is safe. Critically, dispatch's planned advisory lock is **dispatch-local**: it cannot stop the desktop app, which knows nothing about it.

## Decision

Authority over a lane is a ladder, not a flag:

- **Owned lanes** (dispatch created them): full read/write, always.
- **Attached lanes** (existing desktop threads): **observe-only by default** — resume, read history, subscribe to events; no `send`/`steer`/`brief`/`interrupt`.
- **idle-only-write** and **full-write** on attached lanes are unlocked only when **(a)** the slice-0 cross-process spike shows it is safe, **and (b)** the user explicitly opts in (per-lane or global).

## Assumptions (must hold; verify before relying)

1. Owned lanes truly have no concurrent external writer.
2. Merely *observing* an attached lane (resume + read events) is safe alongside the desktop app — to be confirmed by the spike, not assumed.
3. The spike can actually distinguish "safe" from "racy" for concurrent turns on a shared thread.

## Consequences

- Honors the user's "full read/write" intent where it is safe today (owned lanes) and stays honest where it is not (attached lanes), instead of shipping an unverified guarantee.
- The advisory lock is treated as intra-dispatch coordination only — never as cross-process safety.

## Alternatives considered

- **Full read/write on attached lanes now** — rejected: unverified; the advisory lock can't gate the desktop app.
- **Own-lanes-only for v1** — rejected: loses the attach-to-existing value the user wants; the ladder keeps it as a gated opt-in instead.

## References

- ADR-0002 (Single Daemon over One App Server); `docs/research/app-server-verification.md` (resume fan-out, cross-process untested); `PLAN.md` Phase-1 slice-0 spike.
