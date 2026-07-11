"""Registry row models (Pydantic). Validation at the storage boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue, StrictInt, StrictStr, TypeAdapter

from outfitter.dispatch.client.models import (
    ApprovalPolicy,
    ApprovalsReviewer,
    Effort,
    Personality,
    ReasoningSummary,
    ThreadSandbox,
)

LaneSource = Literal["own", "attached"]
LaneStatus = Literal[
    "idle",
    "busy",
    "waiting_approval",
    "waiting_input",
    "waiting_elicitation",
    "waiting_tool",
    "archived",
    "error",
    "unknown",
]
TurnRuntimeStatus = Literal["started", "completed", "failed"]
ProviderTurnStatus = Literal["started", "completed", "failed", "unknown"]
ProviderThreadLifecycleState = Literal["active", "archived", "deleted", "unknown"]
ProviderCapacityState = Literal[
    "ready", "partial", "signed_out", "unsupported", "unavailable", "disabled"
]
SyncState = Literal["unknown", "metadata", "partial", "complete", "error"]
HistoryCapability = Literal["unknown", "supported", "turn-page-fallback", "unsupported"]
QueuedMessageStatus = Literal["pending", "sending", "sent", "error"]
MessageReceiptStatus = Literal["created", "sent", "accepted", "completed", "failed", "timed_out"]
ServiceTierSource = Literal["dispatch", "configured_default", "observed", "unknown"]
InboxMessageState = Literal["pending", "acked", "archived"]
InboxMessageKind = Literal[
    "subscription_update", "direct_message", "system_notice", "trigger_result", "reminder"
]
SubscriptionWhen = Literal[
    "done", "completed", "failed", "needs-attention", "approval", "idle", "activity"
]
SubscriptionDelivery = Literal["turn", "inbox"]
SubscriptionDeliverPolicy = Literal["idle", "now"]
SubscriptionAckPolicy = Literal["auto", "manual"]
SubscriptionState = Literal["active", "done", "paused"]
ServerRequestState = Literal["pending", "responding", "responded", "denied", "timed_out", "failed"]
ServerRequestOutcome = Literal["responded", "denied", "timed_out", "failed"]

SERVER_REQUEST_TEXT_LIMIT = 1024


class ServiceTierEntry(BaseModel):
    id: str
    name: str
    description: str


class ModelCatalogEntry(BaseModel):
    id: str
    provider: str = "openai"
    display_name: str | None = None
    description: str | None = None
    is_default: bool | None = None
    hidden: bool | None = None
    default_reasoning_effort: str | None = None
    supported_reasoning_efforts: list[str] = Field(default_factory=list)
    default_service_tier: str | None = None
    service_tiers: list[ServiceTierEntry] = Field(default_factory=list)
    additional_speed_tiers: list[str] = Field(default_factory=list)
    input_modalities: list[str] = Field(default_factory=list)
    supports_personality: bool | None = None
    upgrade: str | None = None
    first_seen_at: str
    last_seen_at: str
    source: str = "app-server"


class PermissionProfileEntry(BaseModel):
    id: str
    cwd: str
    description: str | None = None
    allowed: bool
    first_seen_at: str
    last_seen_at: str
    source: str = "app-server"


class LaneModelSettings(BaseModel):
    lane: str
    model_provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    requested_service_tier: str | None = None
    resolved_service_tier: str | None = None
    service_tier_name: str | None = None
    service_tier_source: ServiceTierSource = "unknown"
    updated_at: str


class LaneRuntimeSettings(BaseModel):
    lane: str
    permission_profile: str | None = None
    sandbox: ThreadSandbox | None = None
    approval_policy: ApprovalPolicy | None = None
    approvals_reviewer: ApprovalsReviewer | None = None
    effort: Effort | None = None
    summary: ReasoningSummary | None = None
    model: str | None = None
    service_tier: str | None = None
    output_schema: dict[str, object] | None = None
    personality: Personality | None = None
    updated_at: str


class ProviderEvent(BaseModel):
    """Append-only provider lifecycle event captured at the registry boundary."""

    id: int | None = None
    provider: str
    provider_thread_id: str
    lane: str | None = None
    event_type: str
    provider_event_id: str | None = None
    provider_turn_id: str | None = None
    provider_item_id: str | None = None
    correlation_id: str | None = None
    provider_ts: str | None = None
    received_at: str
    summary: dict[str, object] = Field(default_factory=dict)
    payload: dict[str, object] | None = None
    raw_retained: bool = False


class ProviderThreadObservation(BaseModel):
    """Sparse provider thread metadata observed during discovery or sync.

    ``lifecycle_state`` is deliberately optional: omitted observations preserve
    the stored lifecycle rather than reviving an archived or deleted thread.
    """

    provider: str = "codex"
    provider_thread_id: str
    session_id: str | None = None
    parent_thread_id: str | None = None
    forked_from_id: str | None = None
    source_kind: str | None = None
    thread_source: str | None = None
    agent_nickname: str | None = None
    agent_role: str | None = None
    agent_depth: int | None = None
    lifecycle_state: ProviderThreadLifecycleState | None = None
    relationship_source: str | None = None
    confidence: float | None = None
    observed_at: str | None = None


class ProviderThread(BaseModel):
    """A provider-owned thread identity retained independently of lanes."""

    provider: str
    provider_thread_id: str
    session_id: str | None = None
    parent_thread_id: str | None = None
    forked_from_id: str | None = None
    source_kind: str | None = None
    thread_source: str | None = None
    agent_nickname: str | None = None
    agent_role: str | None = None
    agent_depth: int | None = None
    lifecycle_state: ProviderThreadLifecycleState = "unknown"
    relationship_source: str | None = None
    confidence: float | None = None
    first_seen_at: str
    last_seen_at: str
    archived_at: str | None = None
    deleted_at: str | None = None


class ProviderThreadNode(BaseModel):
    """A provider thread enriched with Dispatch lane ownership when available."""

    thread: ProviderThread
    managed: bool
    ref: str | None = None
    handle: str | None = None
    lane_status: LaneStatus | None = None


class ProviderCapacityWindow(BaseModel):
    limit_id: str
    limit_name: str | None = None
    window: str
    used_percent: int | None = Field(default=None, ge=0, le=100)
    remaining_percent: int | None = Field(default=None, ge=0, le=100)
    duration_minutes: int | None = Field(default=None, ge=0)
    resets_at: int | None = None
    reached_type: str | None = None
    observed_at: str


class ProviderResetCredit(BaseModel):
    fingerprint: str
    reset_type: str
    status: str
    granted_at: int
    expires_at: int | None = None
    title: str | None = None


class ProviderUsageSummary(BaseModel):
    lifetime_tokens: int | None = None
    current_streak_days: int | None = None
    longest_streak_days: int | None = None
    peak_daily_tokens: int | None = None
    longest_running_turn_seconds: int | None = None


class ProviderDailyUsage(BaseModel):
    start_date: str
    tokens: int = Field(ge=0)


class ProviderCapacityObservation(BaseModel):
    provider: str
    host_scope: str = "local"
    config_scope: str = "default"
    state: ProviderCapacityState
    account_type: str | None = None
    account_fingerprint: str | None = None
    account_label: str | None = None
    plan: str | None = None
    requires_auth: bool | None = None
    windows: list[ProviderCapacityWindow] = Field(default_factory=list)
    reset_credits_available: int | None = Field(default=None, ge=0)
    reset_credits: list[ProviderResetCredit] = Field(default_factory=list)
    usage_summary: ProviderUsageSummary | None = None
    daily_usage: list[ProviderDailyUsage] = Field(default_factory=list)
    has_credits: bool | None = None
    unlimited_credits: bool | None = None
    source: list[str] = Field(default_factory=list)
    observed_at: str
    account_observed_at: str | None = None
    capacity_observed_at: str | None = None
    usage_observed_at: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    error: str | None = None


class ServerRequest(BaseModel):
    """A compact, durable correlation record for a Codex server request.

    Request payloads are intentionally excluded: the registry retains only the
    metadata needed to recover pending work and arbitrate a single outcome.
    """

    id: int | None = None  # local operator selector, assigned by the registry
    provider: Literal["codex"] = "codex"
    provider_session_id: str  # unique for one App Server connection lifetime
    provider_thread_id: str | None = None
    lane: str | None = None
    request_id: StrictInt | StrictStr
    method: str
    category: str
    state: ServerRequestState = "pending"
    received_at: str
    deadline_at: str | None = None
    resolved_at: str | None = None
    response_summary: str | None = Field(default=None, max_length=SERVER_REQUEST_TEXT_LIMIT)
    error: str | None = Field(default=None, max_length=SERVER_REQUEST_TEXT_LIMIT)


class ThreadTurn(BaseModel):
    """Normalized turn lifecycle facts derived from provider events/history."""

    provider: str
    provider_thread_id: str
    turn_id: str
    lane: str | None = None
    status: ProviderTurnStatus = "unknown"
    started_at: str | None = None
    completed_at: str | None = None
    failed_at: str | None = None
    error: str | None = None
    completion_source: str | None = None
    updated_at: str


class ThreadItem(BaseModel):
    """Normalized history item indexed from provider transcript/history data."""

    provider: str
    provider_thread_id: str
    item_id: str
    lane: str | None = None
    turn_id: str | None = None
    item_type: str
    role: str | None = None
    phase: str | None = None
    status: str | None = None
    text: str | None = None
    tool: str | None = None
    server: str | None = None
    command: str | None = None
    cwd: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    arguments: JsonValue = None
    success: bool | None = None
    agent_nickname: str | None = None
    agent_role: str | None = None
    created_at: str | None = None
    position: int | None = None
    inserted_at: str
    payload: dict[str, object] | None = None
    raw_retained: bool = False


class ThreadItemRef(BaseModel):
    """Queryable reference extracted from a normalized history item."""

    provider: str
    provider_thread_id: str
    item_id: str
    ref_type: str
    ref_value: str


class MessageReceipt(BaseModel):
    """Lifecycle of a Dispatch-originated message as providers accept/finish it."""

    id: int | None = None
    lane: str | None = None
    queued_message_id: int | None = None
    provider: str
    provider_thread_id: str
    dispatch_message_id: str | None = None
    status: MessageReceiptStatus = "created"
    turn_id: str | None = None
    error: str | None = None
    created_at: str
    sent_at: str | None = None
    accepted_at: str | None = None
    completed_at: str | None = None
    failed_at: str | None = None
    updated_at: str


class LaneRuntimeState(BaseModel):
    """Compact derived runtime state fed by provider events and reducers."""

    lane: str
    provider: str
    provider_thread_id: str
    status: LaneStatus = "unknown"
    active_turn_id: str | None = None
    latest_turn_id: str | None = None
    latest_turn_status: TurnRuntimeStatus | None = None
    needs_attention: bool = False
    attention_kind: str | None = None
    attention_detail: str | None = None
    updated_at: str
    last_event_at: str | None = None


class Lane(BaseModel):
    """A managed Codex thread — one row of the ``lanes`` table."""

    id: str  # the App Server threadId
    ref: str  # dispatch-local stable short ref
    ref_source: str
    ref_payload: str
    ref_mixer: str
    handle: str  # "@name" (own) or "→ @project:name" / desktop title (attached)
    role: str | None = None
    cwd: str | None = None
    source: LaneSource
    status: LaneStatus = "unknown"
    pinned: bool = False
    active_turn_id: str | None = None  # set by the reactor (Phase 3); needed to steer/interrupt
    latest_turn_id: str | None = None
    latest_turn_status: TurnRuntimeStatus | None = None
    latest_error: str | None = None
    latest_error_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_event_at: datetime | None = None


class ActionRecord(BaseModel):
    """One row of the ``actions_log`` — the audit of every send/action."""

    id: int | None = None
    ts: datetime
    op: str
    lane: str | None = None
    trigger_id: str | None = None
    detail: str | None = None  # request/decision summary (JSON or text)
    outcome: str = "ok"  # "ok" or a DispatchError code


class QueuedMessage(BaseModel):
    """A durable queued lane message waiting for the lane to become idle."""

    id: int
    lane: str
    text: str
    content: list[dict[str, JsonValue]] = Field(default_factory=list)
    status: QueuedMessageStatus = "pending"
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class InboxMessage(BaseModel):
    """A durable recipient-facing coordination message."""

    id: int
    recipient_lane: str
    source_lane: str | None = None
    subscription_id: str | None = None
    kind: InboxMessageKind = "system_notice"
    subject: str
    body: str
    payload: dict[str, object] = Field(default_factory=dict)
    state: InboxMessageState = "pending"
    delivery: SubscriptionDelivery = "inbox"
    queued_message_id: int | None = None
    created_at: datetime
    delivered_at: datetime | None = None
    acked_at: datetime | None = None


class Subscription(BaseModel):
    """A durable event-to-inbox binding."""

    id: str
    target_lane: str
    subscriber_lane: str
    when: SubscriptionWhen = "done"
    delivery: SubscriptionDelivery = "turn"
    deliver: SubscriptionDeliverPolicy = "idle"
    tail: int = Field(default=1, ge=0)
    once: bool = True
    ack: SubscriptionAckPolicy = "auto"
    attribution: bool = True
    state: SubscriptionState = "active"
    created_at: datetime
    updated_at: datetime
    last_matched_at: datetime | None = None
    last_inbox_message_id: int | None = None


class LaneSync(BaseModel):
    """Compact sync/index state for a managed lane."""

    lane: str
    state: SyncState
    source_path: str | None = None
    source_device: int | None = None
    source_inode: int | None = None
    source_size: int | None = None
    source_mtime_ns: int | None = None
    line_count: int | None = None
    first_offset: int | None = None
    tail_offset: int | None = None
    next_offset: int | None = None
    last_synced_at: str | None = None
    error: str | None = None
    display_name: str | None = None
    preview: str | None = None
    cwd: str | None = None
    source: str | None = None
    thread_source: str | None = None
    model_provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    session_id: str | None = None
    latest_event_at: str | None = None
    latest_turn_id: str | None = None
    transcript_partial: bool = True
    history_source: str | None = None
    history_cursor: str | None = None
    history_backwards_cursor: str | None = None
    history_recent_cursor: str | None = None
    history_pending_backwards_cursor: str | None = None
    history_item_turn_id: str | None = None
    history_item_turn_cursor: str | None = None
    history_item_turn_direction: Literal["asc", "desc"] | None = None
    history_item_cursor: str | None = None
    history_cursor_guard: str | None = None
    history_complete: bool = False
    history_capability: HistoryCapability = "unknown"
    observation_enabled: bool = False
    pages_scanned: int = 0
    turns_indexed: int = 0
    items_indexed: int = 0
    unchanged_skipped: bool = False
    scanned_bytes: int = 0
    duration_ms: int = 0
    truncated: bool = False


# --- triggers -----------------------------------------------------------------
# A trigger binds when -> action -> lane (ADR-0003: our own scheduler/reactor).


class IntervalWhen(BaseModel):
    kind: Literal["interval"] = "interval"
    seconds: float = Field(gt=0)


class CronWhen(BaseModel):
    kind: Literal["cron"] = "cron"
    expr: str  # croniter format (NOT iCal RRULE; ADR-0003)


class IdleForWhen(BaseModel):
    kind: Literal["idle_for"] = "idle_for"
    seconds: float = Field(gt=0)  # lane idle for >= seconds


class EventWhen(BaseModel):
    kind: Literal["event"] = "event"
    event: Literal["turn_completed", "waiting_on_approval"]


When = Annotated[IntervalWhen | CronWhen | IdleForWhen | EventWhen, Field(discriminator="kind")]


class SendAction(BaseModel):
    kind: Literal["send"] = "send"
    text: str


class SteerAction(BaseModel):
    kind: Literal["steer"] = "steer"
    text: str


class BriefAction(BaseModel):
    kind: Literal["brief"] = "brief"
    text: str


Action = Annotated[SendAction | SteerAction | BriefAction, Field(discriminator="kind")]


class Guard(BaseModel):
    idle_only: bool = False
    min_interval: float | None = None  # seconds; suppress refiring within the window
    # Suppress firing identical to the immediately previous one. NOTE: dedupe state
    # is process-local (in the runner), so it resets on daemon restart.
    dedupe: bool = False


class Trigger(BaseModel):
    id: str
    name: str
    lane: str  # lane id or @handle (single-lane selector in v0)
    when: When
    action: Action
    guard: Guard = Field(default_factory=Guard)
    enabled: bool = True
    created_at: datetime | None = None  # set by the store; the scheduling baseline
    last_fired_at: datetime | None = None


WhenAdapter: TypeAdapter[When] = TypeAdapter(When)
ActionAdapter: TypeAdapter[Action] = TypeAdapter(Action)
