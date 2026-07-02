"""Registry row models (Pydantic). Validation at the storage boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from outfitter.dispatch.client.models import (
    ApprovalPolicy,
    ApprovalsReviewer,
    Effort,
    Personality,
    ReasoningSummary,
    ThreadSandbox,
)

LaneSource = Literal["own", "attached"]
LaneStatus = Literal["idle", "busy", "waiting_approval", "archived", "error", "unknown"]
TurnRuntimeStatus = Literal["started", "completed", "failed"]
ProviderTurnStatus = Literal["started", "completed", "failed", "unknown"]
SyncState = Literal["unknown", "metadata", "partial", "complete", "error"]
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
    text: str | None = None
    tool: str | None = None
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
