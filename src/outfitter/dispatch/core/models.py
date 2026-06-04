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
    ThreadGoalStatus,
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


class ShowInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")
    include_transcript: bool = Field(
        default=False, description="Include a compact transcript from thread/read."
    )
    max_items: int = Field(default=20, ge=1, description="Max transcript items to return.")


class WatchInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")
    limit: int = Field(default=20, ge=1, description="Max live App Server events to collect.")
    timeout: float = Field(
        default=10.0, ge=0.0, description="Seconds to wait for live events before returning."
    )


class TranscriptInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")
    limit: int = Field(default=50, ge=1, description="Max compact transcript items to return.")


class GoalGetInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")


class GoalSetInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")
    objective: str | None = Field(default=None, description="Goal objective text.")
    status: ThreadGoalStatus | None = Field(default=None, description="Goal status.")
    token_budget: int | None = Field(default=None, ge=1, description="Optional token budget.")


class GoalClearInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")


class ForkInput(BaseModel):
    lane: str = Field(description="Source lane id or @handle.")
    name: str = Field(description="Name for the new forked lane.")
    cwd: str | None = Field(default=None, description="Working directory override for the fork.")
    sandbox: ThreadSandbox | None = Field(
        default=None, description="Sandbox override for the fork."
    )
    approval_policy: ApprovalPolicy | None = Field(
        default=None, description="Approval policy override for the fork."
    )
    approvals_reviewer: ApprovalsReviewer | None = Field(
        default=None, description="Approval reviewer override for the fork."
    )
    model: str | None = Field(default=None, description="Model override for the fork.")
    model_provider: str | None = Field(default=None, description="Model provider override.")
    base_instructions: str | None = Field(default=None, description="Base instructions override.")
    developer_instructions: str | None = Field(
        default=None, description="Developer instructions override."
    )
    service_tier: str | None = Field(default=None, description="Service tier override.")
    ephemeral: bool = Field(default=False, description="Create an ephemeral fork.")


class RollbackInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")
    turns: int = Field(default=1, ge=1, description="Turns to drop from the end of history.")


class CompactInput(BaseModel):
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
    transcript: list[TranscriptItem] = Field(default_factory=list)


class TranscriptItem(BaseModel):
    turn_id: str | None = None
    item_id: str | None = None
    type: str
    text: str | None = None


class WatchEvent(BaseModel):
    method: str
    params: dict[str, object] = Field(default_factory=dict)
    request_id: int | None = None


class WatchOutput(BaseModel):
    lane: str
    events: list[WatchEvent]
    timed_out: bool


class TranscriptOutput(BaseModel):
    lane: str
    items: list[TranscriptItem]


class Goal(BaseModel):
    thread_id: str
    objective: str
    status: ThreadGoalStatus
    tokens_used: int
    time_used_seconds: int
    created_at: int
    updated_at: int
    token_budget: int | None = None


class GoalView(BaseModel):
    lane: str
    goal: Goal | None = None


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
