"""Small provider-neutral evidence vocabulary for delivery and readiness facts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from .delivery import DeliveryReceipt

BoundedId = Annotated[str, Field(min_length=1, max_length=500)]
ObservationSource = Literal["submit_result", "live", "history", "read"]
ObservationKind = Literal[
    "accepted", "started", "completed", "failed", "interrupted", "uncertain", "readiness"
]
ReadinessState = Literal["ready", "busy", "unavailable", "unknown"]


class ProviderCorrelation(BaseModel):
    """Opaque identifiers that must match a frozen request before transition."""

    delivery_id: BoundedId | None = None
    correlation_id: BoundedId | None = None
    native_submission_id: BoundedId | None = None
    native_run_id: BoundedId | None = None


class ProviderObservation(BaseModel):
    """Normalized evidence; native payload parsing remains in provider adapters."""

    provider: BoundedId
    binding_id: BoundedId
    native_session_id: BoundedId
    kind: ObservationKind
    correlation: ProviderCorrelation = Field(default_factory=ProviderCorrelation)
    generation: BoundedId | None = None
    source: ObservationSource
    provider_time: datetime | None = None
    received_at: datetime
    partial: bool = False
    readiness: ReadinessState | None = None
    reason: Annotated[str, Field(max_length=2000)] | None = None

    @field_validator("provider_time", "received_at")
    @classmethod
    def _require_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("observation times must include a timezone")
        return value

    @model_validator(mode="after")
    def _validate_readiness_shape(self) -> Self:
        if (self.kind == "readiness") != (self.readiness is not None):
            raise ValueError("readiness is required only for readiness observations")
        return self


class ReceiptTransition(BaseModel):
    """Visible outcome of applying one observation to one named receipt."""

    receipt: DeliveryReceipt | None = None
    matched: bool
    changed: bool = False
    ended_active_turn: bool = False
    reason: Annotated[str, Field(max_length=200)] | None = None
