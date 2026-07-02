# Goal Spec: daemon-skew-self-heal

Date: 2026-07-02
Status: Ready

## Objective

Implement guarded Dispatch daemon/client version-skew self-healing so a newer CLI can detect a stale daemon, restart it when idle, retry once, and explain clearly when restart is unsafe.

## Context

After new ops land, an already-running `dispatchd` can keep serving the old op registry. The live repro after `dispatch query` landed was `dispatch: unknown op 'query'` until `dispatch down && dispatch up`. That should become a diagnosable skew path, not a confusing user failure.

## Scope

### In

- `DIS-28`: daemon/client skew detection and explanation.
- A control metadata/handshake path that reports daemon version and supported ops.
- CLI-side guarded restart/retry for missing-op skew when the daemon is idle.
- `dispatch daemon restart` or equivalent explicit restart command if needed.
- Tests, docs, skill guidance, local review, PR, merge, and tracker reconciliation.

### Out

- Remote daemon protocol.
- LaunchAgent installation changes.
- App Server protocol changes.
- Restarting while live work is active.
- Release publishing.

## Source Of Truth

- `DIS-28` - tracker issue for daemon/client version skew.
- `src/outfitter/dispatch/surfaces/cli.py` - CLI control-socket caller and lifecycle commands.
- `src/outfitter/dispatch/daemon/control.py` - daemon control server and op dispatch.
- `src/outfitter/dispatch/daemon/lifecycle.py` - singleton start/stop helpers.
- `src/outfitter/dispatch/core/ops.py` - active op registry.
- `docs/adrs/0008-control-socket-protocol.md` - control protocol versioning intent.
- `docs/adrs/0009-mcp-daemon-lifecycle.md` - detached singleton lifecycle.

## Acceptance Criteria

- A stale daemon that does not support the requested op is recognized as likely daemon/client skew.
- If the daemon is idle, the CLI restarts it, retries the original op once, and tells the user what happened.
- If the daemon is busy or cannot prove it is idle, the CLI does not restart silently and prints a clear recovery command.
- The daemon exposes minimal metadata needed for skew detection without running an op handler.
- `dispatch daemon status --json` reports version/op metadata or a clear equivalent.
- Tests cover missing-op skew, idle self-heal, busy/no-self-heal, and restart failure.
- Docs and skills mention the new behavior and manual recovery.
- Local review has no unresolved P0/P1/P2 before merge.

## Decisions

- Default restart policy is conservative: self-heal only when idle and retry once.
- Missing-op skew is a protocol/lifecycle issue, not a business op validation error.
- Busy includes any active/busy/waiting-approval lanes or queued/sending messages if cheaply available.

## Risks

- Restarting at the wrong time could disrupt active work.
- Treating every unknown op as skew could hide a typo; only retry for ops known to the current CLI registry.
- Lifecycle helpers must not signal unrelated processes.
