---
id: 0026
slug: claude-control-uses-resume-processes-and-hooks
title: Claude Control Preserves One Owner and Requires Aggregate Receipts
status: proposed
created: 2026-07-15
updated: 2026-07-15
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0026: Claude Control Preserves One Owner and Requires Aggregate Receipts

## Context

Dispatch's operation, event, receipt, queue, and history substrate is becoming
provider-neutral, but its control runtime still calls one Codex App Server
client directly. Claude Code 2.1.210 exposes several possible control paths:
print/stream JSON, durable UUID resume, Agent View/background sessions, Remote
Control, hooks, interactive PTYs, and zmx.

Disposable experiments proved that a caller-chosen UUID survives fresh-process
resume; `UserPromptSubmit` and `Stop` share a provider `prompt_id`; an owned
process can be interrupted and the UUID resumed; and concurrent/duplicate resume
processes create independent turns. Later coexistence probes showed that this is
unsafe when another client remains attached: an ordinary TUI and a fresh resume
both completed but continued from different histories, and the external turn was
absent from both the TUI and a later resume after the TUI wrote again. A persistent
stream-JSON process completed multiple turns coherently while it remained the
exclusive owner, but a concurrent resume split from it in the same way.

Agent View provides durable human supervision and rejects fresh resume while its
background owner lives, preserving coherent ownership. Crew separately proves a
guarded quick-reply UI route into that owner: exact row resolution, detail-title
verification, return/reselection, literal-space reply open, reply-prompt
verification, and submit. zmx 0.6.0 raw send is unacknowledged, can exit zero
after loss, and always logs PTY input bytes; it needs hardening before it can host
that cockpit for Dispatch.

## Decision

Prefer one persistent, unscoped zmx-hosted `claude agents` cockpit for
human-coexistent control while Agent View retains background-worker ownership.
This is one global shared cockpit per local Claude runtime, not one cockpit per
repository. Production launches omit `--cwd`; that flag is reserved for
disposable tests or an explicit operator filter. Treat the unscoped global
roster from `claude agents --json --all` as identity input and cwd/worktree as
mutable routing metadata, not a cockpit boundary.

Port Crew's guarded quick-reply route onto revisioned VT snapshots and serialized
conditional input. Resolve a target by joining provider-qualified full session
UUID, current cwd/worktree metadata, and exactly one visible row. A missing,
duplicate, or ambiguous join fails closed before input. Require exact
roster/session identity and screen guards before every navigation step. Make
payload plus Enter one atomic acknowledged transaction; abort before payload if
human input changes the VT revision. zmx is terminal transport only, not Claude
receipt authority.

Claude's documented Agent View behavior owns reply delivery and queuing: Space
opens peek, Enter submits to the selected session, an ordinary undeliverable
reply is saved as its next prompt, and replying to an exited row restarts the
session through the supervisor. zmx owns only the guarded screen/input path into
that supported UI. Hooks plus owned provider activity remain receipt authority.

This preferred route remains blocked behind DIS-54 until a pinned zmx build
provides bounded current-screen snapshots with viewport/generation metadata,
named keys, monotonic revisions, conditional atomic input ACK, nonzero
loss/overflow errors, complete input-log redaction, and an automation/human lease
that fences zmx leader changes, and until Agent View exposes aggregate hook
settlement plus owned provider activity without raw transcript reads.

Implement the verified headless fallback around one exclusive persistent
`claude --print --input-format stream-json --output-format stream-json` owner.
New sessions use an explicit `--session-id <uuid>`; after a proven owner exit,
restart uses `--resume <uuid>` to create the next exclusive owner. Dispatch
persists identity and the message envelope before writing a frame and never has
more than one owner or in-flight message per Claude session.

Do not represent the headless fallback as seamless human coexistence. Human attach
requires an explicit ownership handoff: stop the Dispatch owner, let the human TUI
be the sole owner, and require explicit hand-back after that TUI exits. Until a
supported shared-owner send primitive or a safe pinned single-PTY transport is
proven, preservation of an already attached human while Dispatch sends is a
blocking capability, not an implementation assumption.

Add Dispatch receipt/attention hooks through generated settings without replacing
or assuming exclusive ownership of existing hooks. Print-mode composition did
not modify user/project settings, but an isolated Agent View launch changed the
user settings file despite isolation flags. Automated Agent View launch stays
disabled until a product isolation contract is proven.

Receipt authority is aggregate:

- Dispatch `UserPromptSubmit` observation -> submission observed only;
- every sibling prompt hook reached a terminal settlement, none blocked, plus
  first owned-stream assistant/tool activity -> processing started; nonblocking
  exit-1/cancelled settlements degrade hook health but do not erase the owned
  processing evidence;
- each `Stop` with the same provider `prompt_id` -> one stop cycle observed;
- final Stop hook settlement without continuation, then terminal per-message
  result success -> main response completed; the owner may remain healthy for
  the next frame;
- `StopFailure` -> provider/API failure;
- owned process SIGINT/exit without final settlement -> interrupted or completion unknown,
  never completed.

Hook failure/timeout is fail-open in Claude. Missing aggregate evidence is
therefore unknown, not rejection. A sibling prompt hook can block after the
Dispatch observer succeeds, and a sibling Stop hook can continue a turn after a
Stop observation. Process exit, stdin write, stream replay,
terminal output, Agent View logs, zmx status, and scrollback are not receipts.

Keep Agent View as the preferred human-supervision/quick-reply owner. Exclude zmx
0.6.0 as shipped and Remote Control from production transport. DIS-54 may admit a
hardened pinned zmx cockpit only with current evidence and explicit
security/product acceptance.

Human Agent View attach is distinct from Dispatch-owned stream ownership/resume.
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

- Uses supported direct CLI/Agent View primitives without private endpoints;
  the coexistence candidate adds one explicit global persistent cockpit PTY.
- Preserves cross-repository visibility and each session's cwd/worktree routing
  metadata without proliferating per-repository cockpits.
- Durable UUID and provider prompt ID give stable routing and exact cycle joins.
- Persistent stream ownership preserves coherent headless multi-turn state and
  avoids the observed per-turn resume split when a second owner is present.
- Agent View cockpit control preserves the same background owner for both human
  and Dispatch input instead of creating a resume competitor.
- Hooks compose with operator settings and feed the existing provider event /
  receipt / attention substrate.
- Single-writer ownership prevents the observed interleaving and duplicate-turn
  failure mode.

### Tradeoffs

- Each owner generation/restart pays process startup cost, and a healthy owner
  remains a long-lived supervised resource between turns.
- There is no true active-turn steer or hidden context injection in the first
  adapter.
- Hook failure can leave processing uncertain even when Claude continues.
- Possible-write or processing-but-incomplete loss cannot be retried automatically.
- Human input waits are observable before a scriptable response path exists.
- Seamless attached-human plus Dispatch send remains blocked until DIS-54 proves
  hardened zmx transactions and aggregate receipt evidence.
- Claude retains local session transcripts under its own retention policy; the
  supported Agent View `rm` operation is not transcript deletion.

## Alternatives considered

- **Fresh resume process per turn** — rejected as the default: safe only when no
  other owner exists; ordinary TUI and stream-owner probes produced split-brain
  histories rather than shared continuity.
- **zmx-owned worker TUI** — not preferred: it duplicates Agent View supervision;
  zmx 0.6.0 also has no delivery ACK,
  silent loss/exit behavior, no idempotency/correlation, and mandatory raw-input
  logging in 0.6.0.
- **zmx-hosted Agent View cockpit** — preferred pending DIS-54: Crew proves the
  guarded UI route, while Dispatch still needs zmx transaction/redaction work and
  aggregate receipt proof.
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
- Crew `packages/core/src/cmux.ts::sendClaudeAgentsMessage` and
  `docs/operating-lessons.md` at local commit `4a24fdb` plus current target-identity
  hardening
- [ADR-0002](0002-single-daemon-over-one-app-server.md)
- [ADR-0006](0006-handler-context-and-di.md)
- [ADR-0007](0007-normalized-internal-lane-events.md)
- [ADR-0023](0023-provider-event-log-and-history-index.md)
- [ADR-0024](0024-provider-thread-topology-is-independent-of-lane-authority.md)
