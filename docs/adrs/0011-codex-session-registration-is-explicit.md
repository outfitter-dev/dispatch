---
id: 0011
slug: codex-session-registration-is-explicit
title: Codex Session Registration Is Explicit
status: proposed
created: 2026-06-03
updated: 2026-06-03
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0011: Codex Session Registration Is Explicit

## Context

dispatch can discover persisted Codex sessions and attach them as lanes, but ADR-0005 keeps attached lanes blocked for turn-writing by default because desktop Codex and dispatch run separate app-server processes over shared state. Automatically "picking up" every new Codex session would surprise users who do not want all agents visible to dispatch, a mesh peer, an MCP client, or automation triggers.

Some users will want the opposite: a smooth path where sessions created in Codex become known to dispatch without manual `attach`. Codex hooks on session/thread start could provide that path by registering a session intentionally at creation time.

## Decision

dispatch does not automatically adopt every discoverable Codex session. Discoverability and registration are separate:

- `discover` lists candidate sessions.
- `attach` registers a session as a managed attached lane.
- A Codex hook may be configured to call a dispatch registration command when a session starts.
- Hook-registered sessions are still attached lanes and inherit the attached-lane authority ladder from ADR-0005.
- Registration policy is explicit: users choose whether hook registration is disabled, prompt-based, repo-scoped, or automatic for trusted scopes.

The hook path should register only metadata dispatch needs to manage the lane: thread id, title/handle candidate, cwd/repo if available, and source. It must not grant write authority merely because the session was hook-registered.

## Consequences

### Positive

- Avoids surprising users by exposing every Codex session to dispatch.
- Gives power users a convenient opt-in path for automatic lane registration.
- Keeps session adoption consistent with the lane authority model.
- Makes future mesh sharing safer because only registered lanes can be addressed remotely.

### Tradeoffs

- Users who want automatic pickup must configure a hook.
- Hook behavior depends on Codex hook availability and payload shape, which should be verified before implementation.
- Registration can race session startup; the command must tolerate duplicate or late registration.

## Alternatives considered

- **Automatically attach every discoverable session** — rejected: too broad, surprising, and unsafe for mesh/automation.
- **Never support automatic registration** — rejected: manual attach is unnecessarily clunky for users who intentionally want dispatch-managed sessions.
- **Hook registration grants full write access** — rejected: authority comes from lane source and explicit policy, not registration mechanism.

## References

- ADR-0005 (Lane Authority Capability Ladder)
- ADR-0007 (Normalized Internal LaneEvent Vocabulary)
- `docs/research/app-server-verification.md`
