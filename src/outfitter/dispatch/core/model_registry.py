"""App-server-backed model catalog and service-tier resolution.

The Codex product may expose a user-facing tier like ``fast`` while the
app-server/API service tier is ``priority``. Keep that mapping in one place and
source it from ``model/list`` instead of hard-coded model names.
"""

from __future__ import annotations

from dataclasses import dataclass

from outfitter.dispatch.client.models import AppModel, ConfigInfo
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import ValidationError
from outfitter.dispatch.registry.models import (
    LaneModelSettings,
    ModelCatalogEntry,
    ServiceTierEntry,
    ServiceTierSource,
)

_NEUTRAL_SERVICE_TIERS = {"auto", "default"}


@dataclass(frozen=True)
class ModelCatalogSnapshot:
    models: list[ModelCatalogEntry]
    config: ConfigInfo
    refreshed_at: str


@dataclass(frozen=True)
class ResolvedModelSettings:
    model_provider: str | None
    model: str | None
    reasoning_effort: str | None
    requested_service_tier: str | None
    resolved_service_tier: str | None
    service_tier_name: str | None
    service_tier_source: ServiceTierSource

    def for_lane(self, lane_id: str, updated_at: str) -> LaneModelSettings:
        return LaneModelSettings(
            lane=lane_id,
            model_provider=self.model_provider,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            requested_service_tier=self.requested_service_tier,
            resolved_service_tier=self.resolved_service_tier,
            service_tier_name=self.service_tier_name,
            service_tier_source=self.service_tier_source,
            updated_at=updated_at,
        )


async def refresh_model_catalog(ctx: Ctx, *, source: str = "app-server") -> ModelCatalogSnapshot:
    config = await ctx.client.config_read()
    models = await ctx.client.model_list()
    now = ctx.registry.now_iso()
    entries = [
        _catalog_entry(model, config=config, refreshed_at=now, source=source) for model in models
    ]
    await ctx.registry.upsert_model_catalog(entries)
    return ModelCatalogSnapshot(models=entries, config=config, refreshed_at=now)


async def resolve_model_settings(
    ctx: Ctx,
    *,
    model: str | None,
    model_provider: str | None,
    reasoning_effort: str | None,
    service_tier: str | None,
) -> ResolvedModelSettings:
    if service_tier is None:
        config = await ctx.client.config_read()
        configured_tier = config.service_tier
        return ResolvedModelSettings(
            model_provider=model_provider or config.model_provider,
            model=model or config.model,
            reasoning_effort=reasoning_effort or config.model_reasoning_effort,
            requested_service_tier=None,
            resolved_service_tier=configured_tier,
            service_tier_name=None,
            service_tier_source="configured_default" if configured_tier else "unknown",
        )

    snapshot = await refresh_model_catalog(ctx)
    provider = model_provider or snapshot.config.model_provider or "openai"
    resolved_model = model or snapshot.config.model
    if resolved_model is None:
        raise ValidationError(
            f"service_tier {service_tier!r} needs a model; set --model or configure a Codex default"
        )

    entry = _find_model(snapshot.models, resolved_model, provider=provider)
    if entry is None:
        available = ", ".join(item.id for item in snapshot.models) or "none"
        raise ValidationError(f"unknown model {resolved_model!r}; available models: {available}")

    resolved_tier, tier_name = _resolve_service_tier(entry, service_tier)
    return ResolvedModelSettings(
        model_provider=provider,
        model=resolved_model,
        reasoning_effort=reasoning_effort or snapshot.config.model_reasoning_effort,
        requested_service_tier=service_tier,
        resolved_service_tier=resolved_tier,
        service_tier_name=tier_name,
        service_tier_source="dispatch",
    )


def _catalog_entry(
    model: AppModel, *, config: ConfigInfo, refreshed_at: str, source: str
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=model.id,
        provider=config.model_provider or "openai",
        display_name=model.display_name or model.name,
        description=model.description,
        is_default=model.is_default,
        hidden=model.hidden,
        default_reasoning_effort=model.default_reasoning_effort,
        supported_reasoning_efforts=model.supported_reasoning_efforts,
        default_service_tier=model.default_service_tier,
        service_tiers=[
            ServiceTierEntry(id=tier.id, name=tier.name, description=tier.description)
            for tier in model.service_tiers
        ],
        additional_speed_tiers=model.additional_speed_tiers,
        first_seen_at=refreshed_at,
        last_seen_at=refreshed_at,
        source=source,
    )


def _find_model(
    models: list[ModelCatalogEntry], model_id: str, *, provider: str
) -> ModelCatalogEntry | None:
    for model in models:
        if model.id == model_id and model.provider == provider:
            return model
    for model in models:
        if model.id == model_id:
            return model
    return None


def _resolve_service_tier(model: ModelCatalogEntry, requested: str) -> tuple[str, str | None]:
    requested_norm = requested.lower()
    if requested_norm in _NEUTRAL_SERVICE_TIERS:
        return requested_norm, requested_norm.title()

    for tier in model.service_tiers:
        if tier.id.lower() == requested_norm or tier.name.lower() == requested_norm:
            return tier.id, tier.name

    if requested_norm == "fast":
        for tier in model.service_tiers:
            if tier.name.lower() == "fast":
                return tier.id, tier.name
        if any(tier.lower() == "fast" for tier in model.additional_speed_tiers):
            return "fast", "Fast"

    available = _available_tiers(model)
    raise ValidationError(
        f"model {model.id!r} does not advertise service_tier {requested!r}; "
        f"available service tiers: {available}"
    )


def _available_tiers(model: ModelCatalogEntry) -> str:
    tiers = [f"{tier.id} ({tier.name})" for tier in model.service_tiers]
    if model.additional_speed_tiers:
        tiers.extend(f"{tier} (deprecated speed tier)" for tier in model.additional_speed_tiers)
    tiers.extend(sorted(_NEUTRAL_SERVICE_TIERS))
    return ", ".join(tiers) if tiers else "none"
