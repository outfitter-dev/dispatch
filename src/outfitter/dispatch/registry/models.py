"""Registry row models (Pydantic). Validation at the storage boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

LaneSource = Literal["own", "attached"]
LaneStatus = Literal["idle", "busy", "waiting_approval", "archived", "error", "unknown"]
QueuedMessageStatus = Literal["pending", "sending", "sent", "error"]


class Lane(BaseModel):
    """A managed Codex thread — one row of the ``lanes`` table."""

    id: str  # the App Server threadId
    handle: str  # "@name" (own) or "→ @project:name" / desktop title (attached)
    role: str | None = None
    cwd: str | None = None
    source: LaneSource
    status: LaneStatus = "unknown"
    pinned: bool = False
    active_turn_id: str | None = None  # set by the reactor (Phase 3); needed to steer/interrupt
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
