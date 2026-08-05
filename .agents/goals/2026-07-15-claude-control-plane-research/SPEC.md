# Goal Spec: Claude Control-Plane Research

Date: 2026-07-15
Status: Active

## Objective

Turn Dispatch's Claude session-control unknowns into verified capabilities, explicit unsupported semantics, or bounded product decisions, then produce an implementation-ready plan for launching, attaching, messaging, interrupting, observing, and recovering Claude sessions through Dispatch.

## Context

Dispatch 0.10.0 can observe Claude account, runtime, and statusline capacity, but every lane-control operation still targets the Codex App Server. DIS-9 captures earlier hook and zmx findings, DIS-48 covers usage-capture installation, and DIS-49 defines provider-selection shorthand. None of those establishes a trustworthy Claude message transport or proves how Codex-shaped operations map to Claude.

Claude Code 2.1.210 advertises background agents, `--resume`, explicit session IDs, named sessions, realtime stream-JSON input/output, hook-event streaming, remote control, permission modes, and Agent View. zmx 0.6.0 provides persistent PTYs, raw input, history, tailing, and process lifetime, but `zmx send` is explicitly fire-and-forget. These are candidates to verify, not accepted architecture.

## Research Questions

### Session identity and lifecycle

- What durable Claude identifiers exist for interactive sessions, background agents, Agent View entries, resumed sessions, forks, worktrees, remote-control sessions, and subprocesses?
- Can Dispatch choose or recover a session ID before first message delivery, and does that ID remain stable across detach, process restart, `--resume`, `--continue`, Agent View, and zmx reattachment?
- What metadata can be discovered without reading transcripts or private state, and which title/cwd/project/status fields are authoritative versus presentation-only?
- What do completion, failure, cancellation, permission wait, user-input wait, archive/retention, and process death look like?

### Launch and transport

- Compare Agent View/background dispatch, interactive PTY, `--resume`, stream-JSON print mode, remote control, zmx, and any supported direct CLI surface for persistent multi-turn control.
- Determine which candidate owns process lifetime, input framing, output framing, backpressure, reconnection, concurrent writers, terminal sizing, cancellation, and crash recovery.
- Prove whether a message sent from a separate process is merely written, accepted by Claude, committed as a user turn, or completed. Do not treat a successful shell exit, PTY write, or scrollback appearance as delivery proof.

### Operation semantics

For `new`, `attach`, `send`, `steer`, `queue`, `interject`, `context`, `stop`, `tail`, `watch`, `rename`, `archive`, `restore`, `goal`, permissions, and structured output, identify the exact Claude primitive, a safe Dispatch composition, or an explicit unsupported result. In particular:

- Does input sent during an active turn steer that turn, queue for the next turn, get rejected, or behave inconsistently?
- Can interrupt plus input reliably implement interject without losing the session or creating duplicate turns?
- Is there any true model-visible context injection distinct from a user message? If not, do not relabel `send` as `context`.
- Can Dispatch's durable queue drain only when Claude is truly ready for input?
- Can a stopped/interrupted session accept another message without attach/resume ambiguity?

### Hooks, receipts, and attention

- Verify the supported schemas and ordering for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Notification`, elicitation/user-input events, `Stop`, `SubagentStop`, and any Agent View or message-display events available in the installed version.
- Determine the earliest event that proves provider acceptance, the event that proves turn completion, and the events that mean human attention is required.
- Prove a correlation envelope using Claude session identity plus a Dispatch message ID without exposing markers to the model unnecessarily or allowing sender spoofing.
- Determine whether per-session `--settings` can add Dispatch hooks while preserving operator settings and whether hook failure can block or corrupt the session.

### Architecture, security, and operations

- Define the provider adapter boundary beneath authored Dispatch operations, including capability negotiation and typed unsupported errors.
- Define lane/provider identity, event normalization, message receipts, runtime state, inbox/attention, queue ownership, and recovery storage without duplicating provider-neutral tables.
- Threat-model PTY injection, shell quoting, control characters, spoofed attribution, hook command execution, settings precedence, transcript leakage, debug logs, concurrent sends, and stale process/session identity.
- Preserve a path to SSH/mesh operation without implementing remote transport in this goal.

## Scope

### In

- Official Claude documentation, installed CLI behavior, Agent View/background agents, hooks, settings, stream JSON, resume/attach behavior, remote control, and zmx behavior.
- Read-only inspection of existing agent metadata when useful; all message/control experiments use disposable sessions in temporary repositories.
- Small reproducible probes, sanitized fixtures, capability matrices, sequence diagrams, research notes, ADRs where decisions are justified, and an implementation milestone plan.
- Scoped Linear reconciliation for DIS-9 and DIS-49 plus creation of focused implementation issues and dependency edges when the evidence supports them.
- Two independent local-review lanes: transport/protocol correctness and security/product semantics.

### Out

- Production Claude adapter code, public provider flags, schema migrations, release/publish, mesh transport, Slack/Linear gateway work, or broad provider plugin architecture.
- Messaging or interrupting existing user sessions, reading raw transcripts, reading auth files/keychain material, calling private endpoints, or modifying global/project Claude settings.
- Treating Claude Agent SDK behavior as proof of direct Claude CLI behavior. SDK material may be compared, but the intended implementation target is the supported Claude CLI/Agent View surface.

## Acceptance Criteria

- A source-backed capability matrix covers every operation and lifecycle question with one of: `verified`, `unsupported`, `product-decision`, or `blocked`, plus confidence, exact evidence, version, and next action. No naked `unknown` remains.
- A disposable walking skeleton proves, or precisely disproves, cross-process launch, durable identity, separate-process message delivery, hook-confirmed acceptance, completion, interruption, resume/attach, and a second message.
- Delivery acceptance and completion are proven without using raw zmx scrollback as the source of truth.
- At least one failure/recovery sequence covers process death or transport loss, duplicate/retried send, concurrent input, and permission or user-input attention.
- An implementation plan defines provider interfaces, operation capability mapping, storage/event contracts, process supervision, settings/hook injection, security boundaries, testing fixtures, rollout, and an ordered issue/PR sequence beginning with the smallest walking skeleton.
- Official documentation and local experiments are distinguished from inference. Contradictions are reproduced or recorded with source/version/date rather than silently resolved by preference.
- Research artifacts, RETRO evidence, focused reviews, `just check`, hosted CI, and relevant Linear issues are current before the research PR leaves draft.

## Decisions

- Completion horizon is `ready-pr`; this goal researches and plans but does not implement production Claude support.
- Use current supported direct Claude CLI surfaces first. zmx is a candidate process/PTY adapter, not a receipt authority.
- Use the cheapest suitable Claude model and minimal prompts for live probes; repeat behavior only enough to establish confidence.
- Existing sessions and live Claude configuration are read-only. Disposable test sessions must be named and cleaned up.
- Unsupported provider semantics are a valid result. Silent Codex fallback or pretending different semantics are equivalent is not.

## Risks

- Claude CLI and Agent View behavior may change faster than published docs; every conclusion needs a version and evidence source.
- Interactive terminal behavior can appear successful while losing, buffering, or reinterpreting input.
- Hooks can observe completion while still failing to prove initial provider acceptance unless correlation is designed carefully.
- Excessive live probing can consume tokens or leave orphan processes; use bounded prompts, explicit budgets where supported, and cleanup ledgers.
- A provider-neutral abstraction can erase important semantic differences; the plan must expose capability differences rather than force false parity.
