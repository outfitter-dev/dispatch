"""Registry row models (Pydantic). Validation at the storage boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LaneSource = Literal["own", "attached"]
LaneStatus = Literal["idle", "busy", "waiting_approval", "archived", "error", "unknown"]


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
