"""Input/output models for the v1 ops. Field descriptions project to CLI help
and MCP ``inputSchema`` descriptions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from outfitter.dispatch.registry.models import LaneSource, LaneStatus

# --- inputs -------------------------------------------------------------------


class OpenInput(BaseModel):
    name: str = Field(description="Lane name; becomes the @handle.")
    cwd: str = Field(default=".", description="Working directory for the lane.")


class AttachInput(BaseModel):
    thread: str = Field(description="App Server threadId of an existing (desktop) lane.")


class LaneTextInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")
    text: str = Field(description="Message text.")


class LaneInput(BaseModel):
    lane: str = Field(description="Lane id or @handle.")


class RosterInput(BaseModel):
    include_archived: bool = Field(default=False, description="Include archived lanes.")


# --- outputs ------------------------------------------------------------------


class LaneRef(BaseModel):
    id: str
    handle: str
    source: LaneSource
    status: LaneStatus


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
