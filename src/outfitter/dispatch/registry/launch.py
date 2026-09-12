"""Typed records for durable provider-session creation reservations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LaneLaunchStatus = Literal["reserved", "creating", "created", "ambiguous", "failed"]


class LaneLaunch(BaseModel):
    """One immutable attempt to create the provider session for a lane."""

    lane: str
    key: str | None = None
    submitted_payload: str
    request_payload: str
    provider: str
    binding_id: str
    generation: str
    status: LaneLaunchStatus = "reserved"
    runtime_session_id: str | None = None
    stored_session_id: str | None = None
    effective_cwd: str | None = None
    first_delivery_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
