"""Typed records for the durable outbound delivery ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DeliveryMode = Literal["send", "queue"]
DeliveryStatus = Literal[
    "queued",
    "submitting",
    "accepted",
    "completed",
    "failed",
    "ambiguous",
]
DeliveryExecutionStatus = Literal["inProgress", "completed", "failed", "interrupted"]


class DeliveryReceipt(BaseModel):
    """One durable attempt to deliver an exact payload to a lane."""

    id: str
    key: str | None = None
    lane: str
    mode: DeliveryMode
    payload: str
    status: DeliveryStatus = "queued"
    execution_status: DeliveryExecutionStatus | None = None
    turn_id: str | None = None
    queue_id: int | None = None
    error: str | None = None
    reconciliation_attempts: int = 0
    created_at: datetime
    updated_at: datetime
