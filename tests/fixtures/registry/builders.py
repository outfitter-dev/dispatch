"""Registry fixture builders.

Prefer these builders over checked-in SQLite files. They keep storage tests
reviewable while still giving future tests stable, reusable shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from outfitter.dispatch.registry.models import (
    LaneModelSettings,
    LaneRuntimeSettings,
    LaneRuntimeState,
    MessageReceipt,
    ModelCatalogEntry,
    ProviderEvent,
    ServiceTierEntry,
    ThreadItem,
    ThreadItemRef,
    ThreadTurn,
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
        input_modalities=["text", "image"],
        supports_personality=True,
        upgrade="gpt-next",
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


def lane_runtime_settings(
    *,
    lane: str = "L1",
    updated_at: str | None = None,
) -> LaneRuntimeSettings:
    return LaneRuntimeSettings(
        lane=lane,
        sandbox="workspace-write",
        approval_policy="on-request",
        approvals_reviewer="user",
        effort="low",
        summary="concise",
        model="gpt-5.5",
        service_tier="priority",
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        personality="pragmatic",
        updated_at=updated_at or fixed_now_iso(),
    )


def provider_event(
    *,
    lane: str = "L1",
    provider_thread_id: str = "thread-1",
    event_type: str = "turn/started",
    provider_event_id: str | None = "event-1",
    provider_turn_id: str | None = "turn-1",
    received_at: str | None = None,
) -> ProviderEvent:
    return ProviderEvent(
        provider="codex",
        provider_thread_id=provider_thread_id,
        lane=lane,
        event_type=event_type,
        provider_event_id=provider_event_id,
        provider_turn_id=provider_turn_id,
        provider_ts="2026-06-11T12:00:00Z",
        received_at=received_at or fixed_now_iso(),
        summary={"status": "started"},
        payload={"method": event_type, "params": {"turnId": provider_turn_id}},
        raw_retained=True,
    )


def thread_turn(
    *,
    lane: str = "L1",
    provider_thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    status: str = "started",
    updated_at: str | None = None,
) -> ThreadTurn:
    return ThreadTurn(
        provider="codex",
        provider_thread_id=provider_thread_id,
        turn_id=turn_id,
        lane=lane,
        status=status,  # type: ignore[arg-type]
        started_at=fixed_now_iso(),
        updated_at=updated_at or fixed_now_iso(),
    )


def thread_item(
    *,
    lane: str = "L1",
    provider_thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    item_id: str = "item-1",
    position: int | None = None,
    inserted_at: str | None = None,
) -> ThreadItem:
    return ThreadItem(
        provider="codex",
        provider_thread_id=provider_thread_id,
        item_id=item_id,
        lane=lane,
        turn_id=turn_id,
        item_type="toolCall",
        text="uv run pytest",
        tool="bash",
        position=position,
        inserted_at=inserted_at or fixed_now_iso(),
        payload={"type": "toolCall", "command": "uv run pytest"},
        raw_retained=True,
    )


def thread_item_ref(
    *,
    provider_thread_id: str = "thread-1",
    item_id: str = "item-1",
    ref_type: str = "tool",
    ref_value: str = "bash",
) -> ThreadItemRef:
    return ThreadItemRef(
        provider="codex",
        provider_thread_id=provider_thread_id,
        item_id=item_id,
        ref_type=ref_type,
        ref_value=ref_value,
    )


def message_receipt(
    *,
    lane: str = "L1",
    provider_thread_id: str = "thread-1",
    dispatch_message_id: str = "dispatch-message-1",
    status: str = "created",
    updated_at: str | None = None,
) -> MessageReceipt:
    now = updated_at or fixed_now_iso()
    return MessageReceipt(
        lane=lane,
        provider="codex",
        provider_thread_id=provider_thread_id,
        dispatch_message_id=dispatch_message_id,
        status=status,  # type: ignore[arg-type]
        created_at=fixed_now_iso(),
        updated_at=now,
    )


def lane_runtime_state(
    *,
    lane: str = "L1",
    provider_thread_id: str = "thread-1",
    updated_at: str | None = None,
) -> LaneRuntimeState:
    return LaneRuntimeState(
        lane=lane,
        provider="codex",
        provider_thread_id=provider_thread_id,
        status="busy",
        active_turn_id="turn-1",
        latest_turn_id="turn-1",
        latest_turn_status="started",
        needs_attention=False,
        updated_at=updated_at or fixed_now_iso(),
        last_event_at=fixed_now_iso(),
    )
