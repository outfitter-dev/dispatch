# Claude provider implementation plan

Status: implementation-ready research plan; no production implementation  
Evidence: [`claude-control-plane-verification.md`](../research/claude-control-plane-verification.md)  
Decision: [ADR-0026](../adrs/0026-claude-control-uses-resume-processes-and-hooks.md)

## Outcome

Add Claude as a fixed second execution provider beneath Dispatch's authored ops.
The first implementation is a vertical walking skeleton: one Claude lane can be
created with a chosen UUID, receive serialized messages through fresh resume
processes, persist structurally corroborated processing/completion receipts, be interrupted,
survive a Dispatch restart, and receive another message.

This is not a provider plugin framework. Codex remains the default. Unsupported
Claude semantics are visible capabilities and typed errors, never Codex fallback
or renamed approximations.

## Decisions already settled

- One `dispatchd` remains the control authority. It owns a fixed provider manager
  with Codex and Claude runtimes.
- Codex continues to use the existing single App Server client. Claude does not
  go through App Server.
- Claude v1 transport is one serialized `claude --resume <uuid> --print`
  subprocess per turn. New sessions use `--session-id <uuid>`.
- Per-invocation `--settings` adds Dispatch hooks without modifying global or
  project settings. Existing hooks continue to run.
- A `UserPromptSubmit` observation is only submission evidence. Processing is
  confirmed after every prompt hook settles successfully and the owned CLI
  stream emits the first assistant/tool activity for that prompt.
- A `Stop` observation is only a stop-cycle boundary. Completion requires the
  final Stop hook set to settle without a continuation, followed by a terminal
  success result and clean owned-process exit with no intervening activity.
- Process/stdin success, stream replay, result text, Agent View logs, PTY
  scrollback, and zmx status are diagnostic only.
- zmx 0.6.0 is not a production transport because raw send is unacknowledged and
  raw PTY input is always logged.
- Agent View is an optional human supervision/attention surface, not the first
  programmatic send transport.
- One provider session has one in-flight Dispatch send. Concurrency is rejected
  or queued before spawning a process.
- Raw hook payloads and transcripts are not retained by default.
- Mesh remains daemon federation. A remote request executes at the daemon that
  owns the Claude runtime/session.

## Provider boundary

Replace direct handler dependence on the Codex-shaped `Ctx.client` with a small
fixed runtime manager. Keep App Server-only methods inside the Codex adapter.

```python
class ProviderRuntime(Protocol):
    provider: ProviderId

    def capabilities(
        self, session: ProviderSession | None
    ) -> ProviderCapabilities: ...

    async def start(self, request: StartRequest) -> ProviderSession: ...
    async def send(self, request: MessageEnvelope) -> TransportAttempt: ...
    async def interrupt(self, request: InterruptRequest) -> InterruptAttempt: ...
    async def recover(
        self, sessions: Sequence[ProviderSession]
    ) -> RecoveryReport: ...
    def events(self) -> AsyncIterator[ProviderEventEnvelope]: ...
    async def close(self) -> None: ...


class ProviderManager(Protocol):
    def runtime(self, provider: ProviderId) -> ProviderRuntime: ...
    def capabilities(
        self, provider: ProviderId, session: ProviderSession | None
    ) -> ProviderCapabilities: ...
```

Do not put `context`, `steer`, goals, rename, archive, rollback, compact, or
history into the required protocol. Those are optional capabilities implemented
by a provider-specific extension or rejected with
`UnsupportedProviderOperationError`.

`Ctx` receives `providers`, while a compatibility property may expose the Codex
client temporarily to narrow migration. New/changed handlers must resolve a
provider runtime before acting. No surface imports a provider runtime.

### Common request and event envelopes

```python
@dataclass(frozen=True)
class ProviderIdentity:
    provider: Literal["codex", "claude"]
    provider_session_id: str


@dataclass(frozen=True)
class MessageEnvelope:
    dispatch_message_id: UUID
    lane_id: UUID
    identity: ProviderIdentity
    attempt: int
    text: str
    process_generation: UUID


@dataclass(frozen=True)
class ProviderEventEnvelope:
    provider: ProviderId
    provider_session_id: str
    process_generation: UUID
    event_type: str
    source_delivery_id: UUID
    ingest_id: UUID
    hook_id: str | None
    provider_prompt_id: str | None
    provider_timestamp: datetime | None
    received_at: datetime
    normalized: Mapping[str, JsonScalar]
```

The text exists only in the outbound message/queue record under the current
product contract. Hook ingestion receives bounded normalized facts, not a
durable prompt digest or raw prompt/tool/message content. The helper generates one
`source_delivery_id` per invocation and reuses it for bounded socket retries; the
daemon assigns a separate `ingest_id` to each arrival. Claude's owned stream `hook_id`
pairs hook starts/responses but is not present in the raw hook payload.

### Provider event ingress

`ProviderEventEnvelope` is the provider-neutral reactor input; it generalizes the
current Codex-shaped `LaneEvent` rather than creating a parallel reducer. Each
runtime emits envelopes into a merged `ProviderManager.events()` stream. The
reactor resolves `(provider, provider_session_id)` to the local lane key, persists
the provider event, runs the common receipt/runtime/attention reducer, and then
publishes a compatibility `LaneEvent` carrying the local lane key to existing
subscriptions and triggers. Codex's current event indexer becomes the Codex
projector into this envelope. Claude's runtime aggregator emits only after joining
helper observations with owned stream hook/activity/result events. No handler or
surface consumes raw Claude hook input.

## Capability negotiation

Current `writable`-derived booleans falsely imply that every owned lane supports
Codex operations. Replace them with the intersection of:

1. provider support;
2. current session/process state;
3. local lane authority;
4. transport and hook health.

Each capability reports:

```json
{
  "supported": true,
  "available_now": false,
  "reason": "message_in_flight"
}
```

Reasons are a bounded enum such as `provider_unsupported`, `attached_read_only`,
`message_in_flight`, `needs_attention`, `hook_unhealthy`, `stale_identity`, or
`provider_unavailable`. CLI and MCP derive the same structure.

Initial Claude capabilities:

- supported: new, owned resume/send, interrupt, live watch, permission observation,
  structured print output;
- composed/product-gated: queue, interject, history/tail, rename, user-input
  response, rich input, remote control;
- unsupported: active-turn steer, Codex context injection, archive/restore,
  Codex goal mutation.

## Identity and registry migration

The existing `lanes.id` is both Dispatch identity and Codex thread ID. Split
those concepts before routing Claude.

### Schema changes

- Keep the physical `lanes.id` column and all existing foreign keys unchanged;
  reinterpret it as the stable Dispatch-local lane key. Existing values remain
  valid local keys, so SQLite does not need a risky primary-key/table rebuild.
- Add `lane_provider_identities(lane_id TEXT PRIMARY KEY REFERENCES lanes(id) ON
  DELETE CASCADE, provider TEXT NOT NULL CHECK(provider IN ('codex','claude')),
  provider_session_id TEXT NOT NULL, UNIQUE(provider, provider_session_id))`.
- In one `BEGIN IMMEDIATE` migration, create the table, backfill every existing
  lane as `('codex', lanes.id)`, verify row counts and uniqueness, then commit.
  Any failed assertion rolls back the entire migration.
- New lanes allocate a random local `lanes.id` and insert the provider identity in
  the same transaction before any provider mutation. All child tables continue to
  reference that local key without rebuild or dual-write.
- Public outputs add `provider`, `provider_session_id`, and `lane_key`. The legacy
  `id`/`lane` fields continue to expose `lanes.id`; for existing Codex rows this is
  unchanged, while callers must use `provider_session_id` for provider identity.
  Contract examples and one release-note deprecation remove the old "full Codex
  thread ID" promise before any future field rename.
- Allocate refs with provider-specific source/payload. Never hash a Claude UUID
  through `codex_ref_payload()`.
- Qualify raw provider selectors (`claude:<uuid>`, `codex:<thread-id>`). An
  unqualified managed ref remains safe; an unqualified unmanaged provider ID is
  rejected once multiple providers are enabled.

`provider_threads` already stores provider topology independently of lane
authority. Reuse it for metadata discovery; discovery never creates a writable
lane.

Rollback before provider rows exist drops only `lane_provider_identities`. After
Claude rows exist, rollback disables Claude launches and retains the additive
table/read compatibility; it never deletes lanes or provider identities. Migration
tests start from the prior schema with lanes plus every child-table relationship,
exercise upgrade and failed-assertion rollback, and verify foreign keys/public
Codex outputs byte-for-byte.

### Runtime state additions

DIS-50 adds three focused tables; it does not put process-manager state into the
provider-neutral `lane_runtime_state` reducer row:

- `provider_runtime_sessions`: one row per lane, keyed by `lane_id`, with provider,
  current generation, pid/pgid/start identity, effective cwd, active attempt ID,
  readiness/confidence, hook health/last timestamp, and recovery state/reason.
- `provider_transport_attempts`: append-only attempts keyed by random `attempt_id`,
  with unique `(lane_id, dispatch_message_id, attempt_number)`, generation,
  frame-write state, transport state, provider-message state, prompt ID, Stop
  occurrence, hook settlement, uncertainty reason, terminal/result/exit facts,
  and optional audited resolution actor/time/reason. A partial unique index permits
  only one nonterminal attempt per lane.
- `provider_runtime_artifacts`: generation/path ownership and the cleanup state
  machine defined below, keyed by `(lane_id, generation, path)`.

Foreign keys target the local lane key with cascade only for explicit lane
deletion. DIS-50 owns creation, migration, idempotent request lookup, basic
monotonic transitions, restart recovery, and uncertainty. DIS-51 hardens replay,
late/out-of-order event reduction, StopFailure, indexes, and fixture breadth; it
does not invent or replace the attempt/session tables.

## Claude runtime and supervision

### Launch

For `new --provider claude`:

1. validate provider availability/version and requested capabilities;
2. allocate `lane_key`, ref, full Claude UUID, and process generation;
3. persist lane/runtime/message state before spawning;
4. create an owner-only runtime directory and generated settings file atomically;
5. spawn with `asyncio.create_subprocess_exec`, an explicit argv, bounded env,
   cwd, `--session-id UUID`, `--print`, stream JSON, Haiku/default model as
   configured, permission mode, and settings path;
6. write one framed message and close stdin;
7. stream bounded structural and hook-settlement events while the Dispatch hook
   provides content-minimized observations;
8. record process exit independently from provider receipt state.

For later sends, replace `--session-id` with `--resume UUID`. Never use
`--continue` for a managed lane.

### Process ownership

The Claude runtime owns every process it spawns. On POSIX, spawn with
`start_new_session=True`, record pid, pgid, process start identity, and generation,
and verify all four before signalling so a reused pid cannot be targeted. Platforms
without equivalent process-group and start-identity checks fail provider startup
as unsupported rather than weakening isolation. On shutdown:

- stop accepting new sends;
- allow a bounded grace interval for a completing process;
- send SIGINT, then TERM, then KILL only to the verified owned process group,
  with bounded waits between stages;
- keep processing-but-incomplete provider state unknown even when an explicit
  operator stop marks the Dispatch queue attempt `abandoned_by_operator`;
- remove generated settings after the process group exits.

Tests use a fake executable with a descendant process and assert the entire group
is gone after escalation. The opt-in Claude scenario repeats the descendant check
against a tool-spawning turn before this provider can be enabled.

Do not signal Agent View supervisor processes or user-launched Claude sessions.

### Restart recovery

At daemon startup, partition lanes by provider. Codex recovery remains in the
App Server supervisor. Claude recovery:

1. invalidates event nonces from prior daemon generations;
2. reconciles persisted pid/pgid/start identity without reading transcripts;
3. marks missing prior processes stopped;
4. preserves session UUIDs as resumable;
5. keeps `processing_started` without completion as `completion_unknown`;
6. resets queued claims but drains only after the runtime is confirmed ready;
7. never replays an ambiguous message automatically.

The next explicit send resumes the UUID in a new generation. A future optional
Agent View adapter may inspect metadata, but it is not required for v1 recovery.

## Hook/settings strategy

Generate one owner-only settings file per active process. `--settings` merges it
above user/project/local settings without modifying them. The file adds command
hooks for the minimum receipt/attention set:

- `SessionStart`, `SessionEnd`;
- `UserPromptSubmit`;
- `Stop`, `StopFailure`;
- `PermissionRequest`, `PermissionDenied`, `Notification`;
- `Elicitation`, `ElicitationResult` when verified on the pinned version;
- optional tool events only when needed for attention/runtime state.

Do not enable `MessageDisplay` or raw transcript capture by default.

Before any prompt frame is written, start the CLI in stream-input mode and require
a `SessionStart` response from the Dispatch helper containing the current
generation nonce. This detects managed settings that disable the hook channel.
Missing or failed preflight aborts before stdin submission and is therefore safely
retryable. Once a prompt frame may have been written, no automatic retry is safe.

The hook command is a packaged `dispatch-provider-hook claude` helper invoked
without user-controlled shell interpolation. Environment contains:

- daemon socket path;
- provider/session ID;
- process generation;
- a random per-generation nonce;
- maximum payload size and schema version.

The helper:

1. reads stdin once with a strict byte limit;
2. validates the event and expected session ID;
3. drops content-bearing prompt fields without hashing or retaining them;
4. extracts bounded event/prompt/tool IDs, enums, counts, and booleans;
5. sends the normalized observation over the owner-only daemon socket;
6. emits the generation nonce and `source_delivery_id` only in its structured hook response so the owned
   CLI stream can identify the Dispatch hook among composed sibling hooks;
7. fails open for observability errors while reporting hook health separately.

The nonce prevents stale-generation and accidental misrouting; it is not an
authentication boundary against same-UID sibling hooks, repository code, or tools.
The security boundary is the OS account plus Claude permission/sandbox policy.
Automatic Claude queue draining is unsupported in the first slice. DIS-52 may
enable it only for canonical roots explicitly allowlisted in Dispatch config; it
must not infer trust by reading Claude private state. Roots outside that persisted
allowlist remain manual-send only regardless of `permission_mode`.

Runtime directories are mode 0700. Generated settings are created with no-follow,
exclusive, atomic mode-0600 writes under generation-specific names. Startup and
rollback sweep only files whose recorded pid/start identity is gone; bounded
retention and `doctor` expose cleanup drift instead of deleting uncertain owners.

Persist one artifact record with generation, path, pid/pgid/start identity, state,
and timestamps. Normal exit moves `active -> cleanup_pending`; removal is attempted
immediately and after 1, 5, and 30 seconds. Startup retries verified-dead pending
artifacts. A verified-dead artifact older than 24 hours becomes `stale`, blocks a
new generation for that lane, and is reported by `doctor`. A destructive
`runtime.cleanup` op re-verifies the recorded owner is gone before removal. If
identity cannot be proven dead, state is `quarantined`, files remain in place,
launch stays blocked, and remediation is to stop/identify that process—not unsafe
deletion. Rollback follows the same state machine.

Provider policy hooks that allow/deny actions are a later explicit feature and
must not share fail-open observability semantics accidentally.

## Receipt and correlation contract

Persist a cryptographically random `dispatch_message_id` before spawn. Enforce a
unique request ID so retries return the existing receipt rather than spawning.

Lifecycle:

```text
created -> transport_started -> frame_not_written|frame_maybe_written
frame_maybe_written -> submission_observed -> processing_started
frame_maybe_written -> acceptance_unknown
processing_started -> stop_observed -> processing_started|completed
processing_started -> completion_unknown|failed|completed
frame_not_written -> failed_before_submission
```

Transport attempt state is separate from provider-message receipt state. A process
can be `interrupted` while provider completion remains unknown. Transitions are
monotonic; an upsert may fill a missing timestamp but never erase/regress a later
status.

Correlation:

1. one-writer lease identifies the only pending envelope for a session;
2. after a frame write begins, loss is `frame_maybe_written` and never auto-retried;
3. the daemon matches the one pending envelope by session and generation;
4. a Dispatch `UserPromptSubmit` delivery stores Claude `prompt_id` as
   `submission_observed`; every sibling prompt hook must reach a terminal outcome,
   no hook may block (exit 2 or blocking decision), and first assistant/tool
   activity must follow before `processing_started`; fail-open exit 1/cancelled
   outcomes degrade hook health but do not erase owned processing evidence;
5. each `Stop`/`StopFailure` joins on session + generation + `prompt_id`; repeated
   Stop deliveries are retained as ordered occurrences, not deduplicated;
6. completion requires the last Stop occurrence, settlement of all hooks for that
   cycle without a continuation, terminal success result, and clean process exit;
7. late events from dead generations are retained as diagnostic provider events
   but cannot mutate the active attempt.

Every helper invocation generates a `source_delivery_id` before its first socket
write and reuses it across bounded retries; the daemon enforces uniqueness on
`(generation, source_delivery_id)`. Each arrival also gets an append-only
`ingest_id`. Owned stream `hook_id` pairs the start and response for one hook
execution, while the helper echoes its source ID in structured response output.
The reducer assigns an occurrence to
`(generation, prompt_id, event_type)` in owned-stream order. Replay dedupe uses
`source_delivery_id`; it never collapses two legitimate Stop hook invocations.

Synthetic visible markers were useful in research but are not needed in
production. If a future transport allows multiple concurrent pending messages,
the correlation contract must be redesigned before lifting the lease.

## Queue, attention, and restart semantics

### Queue

The durable queue remains Dispatch-owned. Claim at most one row per Claude lane.
Do not use legacy `lanes.status == idle`, process liveness, zmx status, or
scrollback as readiness.

Claude is ready only when:

- no active owned process/attempt exists;
- the last attempt is terminal;
- no unresolved permission/elicitation attention blocks the lane;
- hook health meets policy;
- if `Stop` reported background tasks/crons, those are empty or explicitly
  allowed by policy.

A frame-maybe-written, acceptance-unknown, or completion-unknown attempt blocks
automatic drain. Only an explicit operator resolution can abandon the Dispatch
attempt and release the queue; the provider completion fact remains unknown in the
audit record.

### Operator recovery for indeterminate attempts

Status/watch expose `dispatch_message_id`, provider UUID, generation, last owned
stream phase, hook-health/settlement summary, process state, uncertainty reason,
and `safe_to_retry=false`; they never expose prompt or transcript content. The
operator may:

1. wait for a late owned-stream/hook event to settle the attempt;
2. stop a still-live verified owned process group, which changes transport state
   but does not manufacture a provider completion fact; or
3. invoke one authored destructive `attempt.resolve` op with
   `resolution="abandon"` and a required reason.

`attempt.resolve` records actor/time/reason, marks only the Dispatch attempt
`abandoned_by_operator`, releases its queue lease, and leaves
`provider_completion=unknown`. The next explicit send is a new message ID and the
CLI/MCP response warns that the abandoned provider turn may have completed. There
is no force-retry action for the same envelope.

### Attention

Map normalized events into the existing inbox/runtime model:

- `PermissionRequest` -> `needs_permission`;
- `Notification(permission_prompt)` -> corroborating attention snapshot;
- `Elicitation` -> `needs_input`;
- hook unhealthy during an attempt -> `receipt_uncertain`;
- Agent View `blocked/waiting` -> metadata corroboration, never sole durable
  request content.

First slice lists attention and tells the operator to attach/use Agent View. A
later issue may add programmatic provider responses only after supported direct
semantics are proven.

### Interrupt and interject

`stop` sends SIGINT only to the exact current owned process group. Process exit
confirms the transport interrupt; absence of `Stop` is expected. Before
`processing_started`, the receipt may be interrupted/failed. After it, provider
completion becomes unknown. An explicit operator stop may additionally record
`abandoned_by_operator` to release Dispatch's queue without pretending the
provider turn did not finish later.

`interject` remains product-gated. If enabled, it is:

1. interrupt;
2. await exact process exit;
3. if processing may have started, require explicit `attempt.resolve(abandon)`;
4. only then release the lease and start a new ordinary message;
5. expose that the prior provider turn may still have completed.

It is not active-turn steer and is not atomic.

## Operation mapping

| Dispatch op | Claude v1 behavior |
| --- | --- |
| `new` | supported with explicit UUID and first serialized print process |
| `attach` | unmanaged ordinary-session attach is unsupported in v1: no content-free metadata validation primitive was proven; human Agent View attach is a separate verified UI |
| `send` | supported, one process/attempt, durable request ID |
| `steer` | typed unsupported |
| `queue` | Dispatch-owned, provider-confirmed readiness |
| `interject` | disabled until product semantics accepted |
| `context` | typed unsupported; do not turn into a user message |
| `stop` | supported for current owned process; Agent View stop is not used |
| `attempt.resolve` | destructive explicit abandonment of an indeterminate Dispatch attempt; provider fact stays unknown |
| `tail` / `watch` | normalized provider events and receipt/runtime changes only |
| `history` | disabled by default pending explicit transcript-retention policy |
| `rename` | initial name optional; later mutation product-gated |
| `archive` / `restore` | typed unsupported; Claude `rm` is not archive |
| goals | Dispatch goal orchestration can send turns; Claude goal mutation unsupported |
| permissions | observe/normalize now; automated decisions later |
| structured output | opt-in print request with JSON schema capability |
| rich input | text first; local file/image contract deferred |

## Config, presets, CLI, and MCP

Canonical authored input:

```toml
provider = "claude"
```

Execution provider is distinct from Codex `model_provider`. Initial vocabulary is
fixed to `codex|claude`; omitting it remains Codex.

DIS-50 adds the canonical authored `provider` enum to `new` because the vertical
slice needs an invocable entry point. It updates contract examples and CLI/MCP
canonical projection only; no shorthand is required for the first slice.

DIS-49 projects `--claude` and `--codex` as CLI-only shorthands for
`new --provider`. Conflicting/multiple selector forms fail before any worktree,
session, registry, or provider mutation. Existing-lane ops do not accept provider
flags; they route by persisted lane identity.

MCP, config, presets, remote schema, and persisted data expose only the canonical
provider enum. Capability output is identical across CLI and MCP. A provider that
is configured but unavailable raises a typed availability error; it never falls
back to Codex.

Claude config is intentionally small:

```toml
[providers.claude]
enabled = false
executable = "claude"
minimum_version = "2.1.210"
default_model = "haiku"
permission_mode = "default"
hook_timeout_seconds = 5
```

Do not add zmx, Remote Control, transcript ingestion, retry, or concurrency knobs
until a shipping feature needs them.

## Fixtures and tests

### Unit and contract

- provider manager resolution and typed unsupported errors;
- capability intersection and surface parity;
- provider-qualified selectors and migration compatibility;
- monotonic receipt transitions and duplicate/out-of-order hooks;
- one-writer lease, request-ID dedupe, queue claim/reset;
- generation fencing, hook-settlement, occurrence ordering, and stale-event rejection;
- hook payload size/schema/redaction tests;
- argv/env construction with shell metacharacters/control bytes;
- no raw prompt/tool/transcript fields persisted.

### Fake runtime

Add a fake Claude executable that consumes the same argv/stdin and emits
deterministic hook calls/events for:

- start/submit/process/Stop-cycle/complete/exit;
- sibling prompt block, hook failure/timeout, and Stop continuation;
- interrupt without Stop;
- StopFailure;
- permission/elicitation attention;
- duplicate and late events;
- process crash before frame write, after possible write, and after processing starts;
- daemon restart and generation change.

Use shared provider contract tests against Codex and Claude fakes where semantics
overlap. Do not bloat the existing App Server fake with Claude methods.

### Sanitized provider fixtures

Promote content-free hook shapes from `spikes/claude/fixtures/` into exercised
test fixtures. Tests must load every checked-in fixture. Pin expected event
schemas to the supported Claude version and keep unknown event types visible.

### Opt-in live scenario

A small temp-repo scenario, outside `just check`, uses Haiku and proves:

- explicit UUID and first message;
- structurally corroborated processing/completion;
- fresh-process second message;
- interrupt and resume;
- duplicate request ID does not create a second provider prompt;
- process cleanup and unchanged live settings hashes.

Never use existing sessions or global/project settings.

## Rollout

1. Ship migration/capability/runtime skeleton behind `providers.claude.enabled =
   false`.
2. Dogfood with opt-in temp projects and inspect only normalized events.
3. Enable explicit `new --provider claude`; keep Codex default.
4. Add provider status/doctor output: executable/version, hook health, active
   process count, last receipt, and cleanup drift—never auth or transcript data.
5. Expand queue/attention only after receipt/restart telemetry is stable.
6. Consider Agent View response, Remote Control, rich input, or zmx only through
   separate evidence/decision issues.

Rollback disables new Claude launches, stops only Dispatch-owned active Claude
processes, preserves provider identities/receipts, and leaves sessions resumable
by UUID. Schema rollback is not destructive; compatibility readers tolerate the
new provider columns.

## Ordered implementation slices

### 1. DIS-50 — Vertical Claude walking skeleton

One PR, not an abstraction-only precursor:

- lane identity migration and provider-qualified routing;
- fixed provider manager with existing Codex adapter;
- Claude runtime launch/resume process owner;
- generated per-process settings and content-minimizing hook with preflight;
- persisted random request ID and monotonic processing/completion receipt;
- `new`, `send`, `stop`, watch/status, restart recovery, second message;
- capability projection and typed unsupported operations;
- fake runtime plus opt-in live temp-repo scenario.

Acceptance: a daemon restart between completed turns preserves the UUID; a later
send completes; duplicate client request ID creates no second Claude prompt;
SIGINT proves transport interruption; after processing starts, provider completion
remains unknown until later evidence or explicit audited abandonment. No raw
content is retained.

### 2. DIS-51 — Receipt and reducer hardening

- attempt table/generation fencing;
- hook health, StopFailure, late/out-of-order events;
- provider-scoped dedupe and receipt monotonicity migration;
- bounded provider event fixtures and privacy tests.

### 3. DIS-52 — Queue and attention

- provider-confirmed readiness;
- single in-flight claim and restart reset;
- permission/notification/elicitation inbox mapping;
- uncertain-attempt operator recovery.

### 4. DIS-53 — Agent View metadata discovery

- provider-qualified unmanaged discovery metadata only;
- Agent View metadata adapter if still supported;
- authority policy and effective-worktree metadata;
- stale identity/cwd recovery.

### 5. DIS-49 — Provider-selection shorthand surfaces

- derived `--claude` / `--codex` CLI shorthands over DIS-50's canonical enum;
- config/presets/schema/help/completion/skill docs;
- parity and conflict tests.

### 6. Optional capabilities

Separate decisions/issues for structured output, history retention, programmatic
attention response, rename, rich input, Remote Control, and any future zmx build.
Unsupported operations remain explicit until their issue closes with evidence.

## Risks and non-goals

Highest risks are wrong-provider routing, stale or same-UID-spoofed hooks, duplicate turns,
receipt regression, raw-content retention, and accidental signalling of a user
process. The first slice must test each boundary.

Non-goals: generalized provider plugins, production zmx, Remote Control server,
mesh/SSH transport, global Claude settings installation, transcript indexing,
provider-driven automatic fallback, release/publish, or changes to Claude auth.
