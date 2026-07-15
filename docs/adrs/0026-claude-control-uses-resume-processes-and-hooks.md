---
id: 0026
slug: claude-control-uses-resume-processes-and-hooks
title: Claude Control Uses Resume Processes and Hooks
status: proposed
created: 2026-07-15
updated: 2026-07-15
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0026: Claude Control Uses Resume Processes and Hooks

## Context

Dispatch's operation, event, receipt, queue, and history substrate is becoming
provider-neutral, but its control runtime still calls one Codex App Server
client directly. Claude Code 2.1.210 exposes several possible control paths:
print/stream JSON, durable UUID resume, Agent View/background sessions, Remote
Control, hooks, interactive PTYs, and zmx.

Disposable experiments proved that a caller-chosen UUID survives fresh-process
resume; `UserPromptSubmit` and `Stop` share a provider `prompt_id`; an owned
process can be interrupted and the UUID resumed; and concurrent/duplicate resume
processes create independent turns. Agent View provides durable human
supervision but no documented non-interactive shell reply. zmx 0.6.0 raw send is
unacknowledged, can exit zero after loss, and always logs PTY input bytes.

## Decision

Implement the first Claude runtime as one serialized, owned
`claude --resume <uuid> --print` subprocess per turn. New sessions use an
explicit `--session-id <uuid>`. Dispatch persists identity and the message
envelope before spawn and never has more than one in-flight process per Claude
session.

Add Dispatch receipt/attention hooks through a generated per-invocation
`--settings` file. Do not modify user or project settings, and do not replace or
assume exclusive ownership of existing hooks.

Receipt authority is aggregate:

- Dispatch `UserPromptSubmit` observation -> submission observed only;
- all prompt hooks settled successfully plus first owned-stream assistant/tool
  activity -> processing started;
- each `Stop` with the same provider `prompt_id` -> one stop cycle observed;
- final Stop hook settlement without continuation, then terminal result success
  and clean owned-process exit -> main response completed;
- `StopFailure` -> provider/API failure;
- owned process SIGINT/exit without `Stop` -> interrupted or completion unknown,
  never completed.

Hook failure/timeout is fail-open in Claude. Missing aggregate evidence is
therefore unknown, not rejection. A sibling prompt hook can block after the
Dispatch observer succeeds, and a sibling Stop hook can continue a turn after a
Stop observation. Process exit, stdin write, stream replay,
terminal output, Agent View logs, zmx status, and scrollback are not receipts.

Keep Agent View as an optional metadata/human-supervision integration. Exclude
zmx 0.6.0 and Remote Control from the initial production transport. Reconsider
either only through a separate decision with current evidence and explicit
security/product acceptance.

Human Agent View attach is distinct from Dispatch-owned `--resume`-for-send.
Unmanaged ordinary-session attach is unsupported in the first adapter because no
content-free metadata validation primitive was proven.

Preflight the Dispatch SessionStart hook response before writing stdin. Retry is
allowed only when no prompt frame write began. Any loss after a possible write is
indeterminate and blocks automatic queue drain until the operator waits for later
evidence or explicitly abandons the Dispatch attempt. Explicit abandonment does
not rewrite unknown provider completion as failure.

Use generation nonces and owner-only sockets/directories to fence stale or
misrouted hook events, but do not claim spoof resistance against same-UID hooks,
repository code, or tools. Owned stream structure plus aggregate settlement is
required corroboration; the OS user and Claude permission/sandbox policy remain
the security boundary.

Place a fixed provider manager under authored operations. Codex continues to use
the existing App Server runtime; Claude has its own process supervisor. Provider
capabilities are explicit and intersected with lane authority and runtime health.
There is no silent fallback or forced Codex semantic parity.

## Consequences

### Positive

- Uses supported direct CLI primitives without private endpoints or a persistent
  PTY dependency.
- Durable UUID and provider prompt ID give stable routing and exact cycle joins.
- Fresh processes simplify ownership, interrupt, cleanup, and daemon restart.
- Hooks compose with operator settings and feed the existing provider event /
  receipt / attention substrate.
- Single-writer ownership prevents the observed interleaving and duplicate-turn
  failure mode.

### Tradeoffs

- Each turn pays process startup cost.
- There is no true active-turn steer or hidden context injection in the first
  adapter.
- Hook failure can leave processing uncertain even when Claude continues.
- Possible-write or processing-but-incomplete loss cannot be retried automatically.
- Human input waits are observable before a scriptable response path exists.
- Claude retains local session transcripts under its own retention policy; the
  supported Agent View `rm` operation is not transcript deletion.

## Alternatives considered

- **Persistent interactive PTY through zmx** — rejected for v1: no delivery ACK,
  silent loss/exit behavior, no idempotency/correlation, and mandatory raw-input
  logging in 0.6.0.
- **Agent View as the primary transport** — rejected for v1: strong supervisor
  and human UI, but no documented scriptable shell reply operation.
- **Remote Control** — deferred: it introduces Anthropic relay, subscription,
  policy, availability, and multi-device semantics without a documented local
  RPC for Dispatch.
- **Claude Agent SDK** — rejected as proof/target: this plan is for the supported
  direct CLI/Agent View surface and must not infer CLI semantics from the SDK.
- **One generic provider plugin framework first** — rejected: two known runtimes
  need a small fixed boundary and vertical slice, not speculative extensibility.
- **Treat process/result success as completion** — rejected: it cannot prove
  provider acceptance and fails under interruption, hook loss, and zmx loss.

## References

- [Claude control-plane verification](../research/claude-control-plane-verification.md)
- [Claude provider implementation plan](../development/claude-provider-plan.md)
- [ADR-0002](0002-single-daemon-over-one-app-server.md)
- [ADR-0006](0006-handler-context-and-di.md)
- [ADR-0007](0007-normalized-internal-lane-events.md)
- [ADR-0023](0023-provider-event-log-and-history-index.md)
- [ADR-0024](0024-provider-thread-topology-is-independent-of-lane-authority.md)
