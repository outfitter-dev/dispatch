"""Input/output models for the v1 ops. Field descriptions project to CLI help
and MCP ``inputSchema`` descriptions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from outfitter.dispatch.client.models import (
    ApprovalPolicy,
    ApprovalsReviewer,
    Effort,
    Personality,
    ReasoningSummary,
    ThreadSandbox,
)
from outfitter.dispatch.registry.models import LaneSource, LaneStatus

# --- inputs -------------------------------------------------------------------


class OpenInput(BaseModel):
    name: str = Field(description="Lane name; becomes the @handle.")
    cwd: str = Field(default=".", description="Working directory for the lane.")


class NewInput(BaseModel):
    name: str = Field(description="Lane name; prefix/presets may decorate it.")
    preset: list[str] = Field(
        default_factory=list, description="Preset(s) to apply, left to right."
    )
    text: str | None = Field(default=None, description="Initial message text.")
    send: bool = Field(default=True, description="Send the initial message when text is present.")
    cwd: str | None = Field(default=None, description="Working directory for config discovery.")
    prefix: str | None = Field(default=None, description="Name prefix template.")
    sandbox: ThreadSandbox | None = Field(default=None, description="Thread sandbox mode.")
    approval_policy: ApprovalPolicy | None = Field(default=None, description="Approval policy.")
    approvals_reviewer: ApprovalsReviewer | None = Field(
        default=None, description="Where approval requests are routed."
    )
    model: str | None = Field(default=None, description="Model override.")
    model_provider: str | None = Field(default=None, description="Model provider override.")
    effort: Effort | None = Field(default=None, description="Initial turn reasoning effort.")
    summary: ReasoningSummary | None = Field(default=None, description="Reasoning summary mode.")
    personality: Personality | None = Field(default=None, description="Personality override.")
    service_tier: str | None = Field(default=None, description="Service tier override.")
    ephemeral: bool | None = Field(default=None, description="Create an ephemeral thread.")
    base_instructions: str | None = Field(default=None, description="Base instructions text.")
    base_file: str | None = Field(default=None, description="Base instructions file.")
    developer_instructions: str | None = Field(
        default=None, description="Developer instructions text."
    )
    developer_file: str | None = Field(default=None, description="Developer instructions file.")


class AttachInput(BaseModel):
    thread: str = Field(description="App Server threadId of an existing (desktop) lane.")


class LaneTextInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")
    text: str = Field(description="Message text.")


class LaneInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")


class RosterInput(BaseModel):
    include_archived: bool = Field(default=False, description="Include archived lanes.")


class DiscoverInput(BaseModel):
    limit: int = Field(default=50, ge=1, description="Max persisted Codex sessions to list.")


# --- outputs ------------------------------------------------------------------


class LaneRef(BaseModel):
    id: str
    handle: str
    source: LaneSource
    status: LaneStatus


class NewLane(LaneRef):
    sent: bool


class LaneDetail(LaneRef):
    cwd: str | None = None
    active_turn_id: str | None = None


class ActionAck(BaseModel):
    lane: str
    op: str
    accepted: bool = True
    detail: str | None = None


class Roster(BaseModel):
    lanes: list[LaneRef]


class DiscoveredSession(BaseModel):
    """A persisted Codex session discoverable via ``thread/list`` — a candidate to
    ``attach``, not (yet) a managed lane. Mirrors the available ``ThreadInfo`` subset."""

    id: str
    name: str | None = None
    preview: str | None = None
    cwd: str | None = None
    status: str | None = None
    source: str | None = None
    ephemeral: bool | None = None


class Discovery(BaseModel):
    sessions: list[DiscoveredSession]


# --- trigger ops --------------------------------------------------------------

TriggerWhenKind = Literal["interval", "cron", "idle_for", "turn_completed", "waiting_on_approval"]
TriggerActionKind = Literal["send", "steer", "brief"]


class TriggerAddInput(BaseModel):
    name: str = Field(description="Trigger name.")
    lane: str = Field(description="Target lane id or @handle.")
    when: TriggerWhenKind = Field(description="Trigger condition.")
    action: TriggerActionKind = Field(description="Action to run when it fires.")
    text: str = Field(description="Action text (prompt or injected context).")
    seconds: float | None = Field(default=None, description="Seconds for interval/idle_for.")
    cron: str | None = Field(default=None, description="Cron expression (croniter) for cron.")
    idle_only: bool = Field(default=False, description="Only fire when the lane is idle.")
    min_interval: float | None = Field(default=None, description="Min seconds between firings.")
    dedupe: bool = Field(default=False, description="Suppress identical consecutive firings.")


class TriggerIdInput(BaseModel):
    id: str = Field(description="Trigger id.")


class TriggerListInput(BaseModel):
    pass


class TriggerView(BaseModel):
    id: str
    name: str
    lane: str
    when: str
    action: str
    enabled: bool


class TriggerList(BaseModel):
    triggers: list[TriggerView]


class TriggerRemoved(BaseModel):
    id: str
    removed: bool


# --- lifecycle ops (status / log) ---------------------------------------------


class StatusInput(BaseModel):
    pass


class StatusOutput(BaseModel):
    lanes: int
    idle: int
    busy: int
    triggers: int
    triggers_enabled: int


class LogInput(BaseModel):
    limit: int = Field(default=20, description="How many recent actions to show.")


class ActionView(BaseModel):
    ts: str
    op: str
    lane: str | None = None
    trigger_id: str | None = None
    outcome: str
    detail: str | None = None


class LogOutput(BaseModel):
    actions: list[ActionView]
