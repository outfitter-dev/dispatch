"""Registry fixture builders.

Prefer these builders over checked-in SQLite files. They keep storage tests
reviewable while still giving future tests stable, reusable shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from outfitter.dispatch.registry.models import (
    LaneModelSettings,
    ModelCatalogEntry,
    ServiceTierEntry,
)


def fixed_now() -> datetime:
    return datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


def fixed_now_iso() -> str:
    return fixed_now().isoformat()


def service_tier_entry(
    *,
    id: str = "priority",
    name: str = "Fast",
    description: str = "1.5x speed, increased usage",
) -> ServiceTierEntry:
    return ServiceTierEntry(id=id, name=name, description=description)


def model_catalog_entry(
    *,
    id: str = "gpt-5.5",
    provider: str = "openai",
    now: str | None = None,
    service_tiers: list[ServiceTierEntry] | None = None,
) -> ModelCatalogEntry:
    seen_at = now or fixed_now_iso()
    return ModelCatalogEntry(
        id=id,
        provider=provider,
        display_name="GPT-5.5",
        is_default=True,
        hidden=False,
        default_reasoning_effort="xhigh",
        supported_reasoning_efforts=["low", "xhigh"],
        default_service_tier="priority",
        service_tiers=service_tiers if service_tiers is not None else [service_tier_entry()],
        additional_speed_tiers=["fast"],
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )


def lane_model_settings(
    *,
    lane: str = "L1",
    updated_at: str | None = None,
) -> LaneModelSettings:
    return LaneModelSettings(
        lane=lane,
        model_provider="openai",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        requested_service_tier="fast",
        resolved_service_tier="priority",
        service_tier_name="Fast",
        service_tier_source="dispatch",
        updated_at=updated_at or fixed_now_iso(),
    )
