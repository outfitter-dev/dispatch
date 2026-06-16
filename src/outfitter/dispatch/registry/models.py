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
SyncState = Literal["unknown", "metadata", "partial", "complete", "error"]
QueuedMessageStatus = Literal["pending", "sending", "sent", "error"]
ServiceTierSource = Literal["dispatch", "configured_default", "observed", "unknown"]


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
