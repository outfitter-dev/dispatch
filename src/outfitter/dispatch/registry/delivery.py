"""Typed records for the durable outbound delivery ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DeliveryMode = Literal["send", "queue"]
DeliveryTransport = Literal["turn", "native_queue"]
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
    transport: DeliveryTransport = "turn"
    provider: str = "codex"
    binding_id: str = "codex-default"
    native_session_id: str | None = None
    correlation_id: str | None = None
    submission_id: str | None = None
    submitted_payload: str | None = None
    payload: str
    status: DeliveryStatus = "queued"
    execution_status: DeliveryExecutionStatus | None = None
    turn_id: str | None = None
    queue_id: int | None = None
    error: str | None = None
    reconciliation_attempts: int = 0
    evidence_source: str | None = None
    evidence_provider_time: datetime | None = None
    evidence_received_at: datetime | None = None
    evidence_partial: bool = False
    evidence_generation: str | None = None
    created_at: datetime
    updated_at: datetime
