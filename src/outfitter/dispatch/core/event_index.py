"""Reducers from normalized provider events into the registry history index."""

from __future__ import annotations

from outfitter.dispatch.client.events import (
    ApprovalRequested,
    GoalCleared,
    GoalUpdated,
    ItemCompleted,
    LaneEvent,
    LaneIdle,
    StatusChanged,
    ThreadCompacted,
    TokenUsageUpdated,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from outfitter.dispatch.registry.models import (
    Lane,
    LaneRuntimeState,
    ProviderEvent,
    ThreadTurn,
)
from outfitter.dispatch.registry.store import Registry

_CODEX_PROVIDER = "codex"


async def index_codex_lane_event(registry: Registry, lane: Lane, event: LaneEvent) -> None:
    """Persist a normalized Codex LaneEvent and reduce compact runtime facts."""

    received_at = registry.now_iso()
    provider_event = ProviderEvent(
        provider=_CODEX_PROVIDER,
        provider_thread_id=lane.id,
        lane=lane.id,
        event_type=_event_type(event),
        provider_event_id=_provider_event_id(event),
        provider_turn_id=_turn_id(event),
        provider_item_id=_item_id(event),
        correlation_id=_correlation_id(event),
        received_at=received_at,
        summary=_summary(event),
        raw_retained=False,
    )
    await registry.record_provider_event(provider_event)

    turn = _thread_turn(lane, event, received_at)
    if turn is not None:
        await registry.upsert_thread_turn(turn)

    state = _runtime_state(lane, event, received_at)
    if state is not None:
        await registry.upsert_lane_runtime_state(state)


def _event_type(event: LaneEvent) -> str:
    if isinstance(event, TurnStarted):
        return "turn/started"
    if isinstance(event, TurnCompleted):
        return "turn/completed"
    if isinstance(event, TurnFailed):
        return "turn/failed"
    if isinstance(event, LaneIdle):
        return "lane/idle"
    if isinstance(event, ApprovalRequested):
        return f"approval/{event.kind}/requested"
    if isinstance(event, ItemCompleted):
        return "item/completed"
    if isinstance(event, StatusChanged):
        return "thread/status/changed"
    if isinstance(event, TokenUsageUpdated):
        return "thread/token-usage/updated"
    if isinstance(event, GoalUpdated):
        return "thread/goal/updated"
    if isinstance(event, GoalCleared):
        return "thread/goal/cleared"
    if isinstance(event, ThreadCompacted):
        return "thread/compacted"
    return event.__class__.__name__


def _provider_event_id(event: LaneEvent) -> str | None:
    if isinstance(event, TurnStarted | TurnCompleted | TurnFailed):
        return _dedupe_id(event, _event_type(event), event.turn_id)
    if isinstance(event, ItemCompleted):
        return _dedupe_id(event, "item/completed", event.item_id)
    if isinstance(event, ApprovalRequested):
        return _dedupe_id(event, f"approval/{event.kind}/requested", str(event.request_id))
    return None


def _dedupe_id(event: LaneEvent, event_type: str, suffix: str | None) -> str:
    return f"codex:{event.lane_id}:{event_type}:{suffix or 'none'}"


def _turn_id(event: LaneEvent) -> str | None:
    if isinstance(event, TurnStarted | TurnCompleted | TurnFailed | ApprovalRequested):
        return event.turn_id
    return None


def _item_id(event: LaneEvent) -> str | None:
    if isinstance(event, ItemCompleted | ApprovalRequested):
        return event.item_id
    return None


def _correlation_id(event: LaneEvent) -> str | None:
    if isinstance(event, ApprovalRequested):
        return str(event.request_id)
    return None


def _summary(event: LaneEvent) -> dict[str, object]:
    if isinstance(event, TurnFailed):
        return {"status": "failed", "message": event.message}
    if isinstance(event, TurnStarted):
        return {"status": "started"}
    if isinstance(event, TurnCompleted):
        return {"status": "completed"}
    if isinstance(event, ApprovalRequested):
        return {"kind": event.kind, "request_id": event.request_id}
    if isinstance(event, StatusChanged):
        return {"active_flags": list(event.active_flags)}
    return {}


def _thread_turn(lane: Lane, event: LaneEvent, now: str) -> ThreadTurn | None:
    if isinstance(event, TurnStarted) and event.turn_id is not None:
        return ThreadTurn(
            provider=_CODEX_PROVIDER,
            provider_thread_id=lane.id,
            lane=lane.id,
            turn_id=event.turn_id,
            status="started",
            started_at=now,
            updated_at=now,
        )
    if isinstance(event, TurnCompleted) and event.turn_id is not None:
        return ThreadTurn(
            provider=_CODEX_PROVIDER,
            provider_thread_id=lane.id,
            lane=lane.id,
            turn_id=event.turn_id,
            status="completed",
            completed_at=now,
            completion_source="codex-event",
            updated_at=now,
        )
    if isinstance(event, TurnFailed) and event.turn_id is not None:
        return ThreadTurn(
            provider=_CODEX_PROVIDER,
            provider_thread_id=lane.id,
            lane=lane.id,
            turn_id=event.turn_id,
            status="failed",
            failed_at=now,
            error=event.message,
            completion_source="codex-event",
            updated_at=now,
        )
    return None


def _runtime_state(lane: Lane, event: LaneEvent, now: str) -> LaneRuntimeState | None:
    if isinstance(event, TurnStarted):
        return _state(
            lane,
            now,
            status="busy",
            active_turn_id=event.turn_id,
            latest_turn_id=event.turn_id,
            latest_turn_status="started",
        )
    if isinstance(event, TurnCompleted):
        return _state(
            lane,
            now,
            status="idle",
            latest_turn_id=event.turn_id,
            latest_turn_status="completed",
        )
    if isinstance(event, TurnFailed):
        return _state(
            lane,
            now,
            status="error",
            latest_turn_id=event.turn_id,
            latest_turn_status="failed",
            needs_attention=True,
            attention_kind="turn_failed",
            attention_detail=event.message,
        )
    if isinstance(event, LaneIdle):
        return _state(lane, now, status="idle")
    if isinstance(event, ApprovalRequested):
        return _state(
            lane,
            now,
            status="waiting_approval",
            active_turn_id=event.turn_id or lane.active_turn_id,
            latest_turn_id=event.turn_id or lane.latest_turn_id,
            latest_turn_status=lane.latest_turn_status,
            needs_attention=True,
            attention_kind="approval",
            attention_detail=event.kind,
        )
    return None


def _state(
    lane: Lane,
    now: str,
    *,
    status: str,
    active_turn_id: str | None = None,
    latest_turn_id: str | None = None,
    latest_turn_status: str | None = None,
    needs_attention: bool = False,
    attention_kind: str | None = None,
    attention_detail: str | None = None,
) -> LaneRuntimeState:
    return LaneRuntimeState(
        lane=lane.id,
        provider=_CODEX_PROVIDER,
        provider_thread_id=lane.id,
        status=status,  # type: ignore[arg-type]
        active_turn_id=active_turn_id,
        latest_turn_id=latest_turn_id,
        latest_turn_status=latest_turn_status,  # type: ignore[arg-type]
        needs_attention=needs_attention,
        attention_kind=attention_kind,
        attention_detail=attention_detail,
        updated_at=now,
        last_event_at=now,
    )
