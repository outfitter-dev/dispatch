"""Reducers from normalized provider events into the registry history index."""

from __future__ import annotations

from outfitter.dispatch.client.events import (
    ApprovalRequested,
    DiffUpdated,
    GoalCleared,
    GoalUpdated,
    ItemCompleted,
    LaneEvent,
    LaneIdle,
    StatusChanged,
    ThreadArchived,
    ThreadCompacted,
    ThreadUnarchived,
    TokenUsageUpdated,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.core.capture import bound_payload, bound_text
from outfitter.dispatch.registry.models import (
    Lane,
    LaneRuntimeState,
    ProviderEvent,
    ThreadTurn,
)
from outfitter.dispatch.registry.store import Registry

_CODEX_PROVIDER = "codex"


async def index_codex_lane_event(
    registry: Registry,
    lane: Lane,
    event: LaneEvent,
    capture: CapturePolicy | None = None,
) -> None:
    """Persist a normalized Codex LaneEvent and reduce compact runtime facts."""

    policy = capture or CapturePolicy()
    retained_payload = _retained_payload(event, policy)
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
        summary=_summary(event, policy),
        payload=retained_payload,
        raw_retained=retained_payload is not None,
    )
    await registry.record_provider_event(provider_event)

    turn = _thread_turn(lane, event, received_at, policy)
    if turn is not None:
        await registry.upsert_thread_turn(turn)

    state = _runtime_state(lane, event, received_at, policy)
    if state is not None:
        await registry.upsert_lane_runtime_state(state)


def _event_type(event: LaneEvent) -> str:
    if isinstance(event, TurnStarted):
        return "turn/started"
    if isinstance(event, TurnCompleted):
        return "turn/completed"
    if isinstance(event, TurnFailed):
        return "turn/failed"
    if isinstance(event, DiffUpdated):
        return "turn/diff/updated"
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
    if isinstance(event, ThreadArchived):
        return "thread/archived"
    if isinstance(event, ThreadUnarchived):
        return "thread/unarchived"
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
    if isinstance(
        event,
        TurnStarted | TurnCompleted | TurnFailed | ApprovalRequested | DiffUpdated,
    ):
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


def _summary(event: LaneEvent, capture: CapturePolicy) -> dict[str, object]:
    if isinstance(event, TurnFailed):
        summary: dict[str, object] = {"status": "failed"}
        if event.turn_id is not None:
            summary["turn_id"] = event.turn_id
        message = bound_text(event.message, capture)
        if message is not None:
            summary["message"] = message.text
            summary["message_original_bytes"] = message.original_bytes
            summary["message_truncated"] = message.truncated
        return summary
    if isinstance(event, TurnStarted):
        return _status_summary("started", turn_id=event.turn_id)
    if isinstance(event, TurnCompleted):
        return _status_summary("completed", turn_id=event.turn_id)
    if isinstance(event, DiffUpdated):
        return _status_summary("updated", turn_id=event.turn_id)
    if isinstance(event, LaneIdle):
        return {"status": "idle"}
    if isinstance(event, ApprovalRequested):
        summary = {
            "status": "requested",
            "kind": event.kind,
            "request_id": event.request_id,
        }
        if event.turn_id is not None:
            summary["turn_id"] = event.turn_id
        if event.item_id is not None:
            summary["item_id"] = event.item_id
        return summary
    if isinstance(event, ItemCompleted):
        return _status_summary("completed", item_id=event.item_id)
    if isinstance(event, StatusChanged):
        return {
            "status": "changed",
            "active_flags": list(event.active_flags),
        }
    if isinstance(event, TokenUsageUpdated):
        return {"status": "updated"}
    if isinstance(event, GoalUpdated):
        return {"status": "updated"}
    if isinstance(event, GoalCleared):
        return {"status": "cleared"}
    if isinstance(event, ThreadCompacted):
        return {"status": "compacted"}
    if isinstance(event, ThreadArchived):
        return {"status": "archived"}
    if isinstance(event, ThreadUnarchived):
        return {"status": "unarchived"}
    return {}


def _retained_payload(event: LaneEvent, capture: CapturePolicy) -> dict[str, object] | None:
    if not capture.should_retain_raw_payload(is_error=_is_error_event(event)):
        return None
    return bound_payload(event.raw_payload, capture).payload


def _is_error_event(event: LaneEvent) -> bool:
    return isinstance(event, TurnFailed)


def _status_summary(
    status: str, *, turn_id: str | None = None, item_id: str | None = None
) -> dict[str, object]:
    summary: dict[str, object] = {"status": status}
    if turn_id is not None:
        summary["turn_id"] = turn_id
    if item_id is not None:
        summary["item_id"] = item_id
    return summary


def _thread_turn(
    lane: Lane, event: LaneEvent, now: str, capture: CapturePolicy
) -> ThreadTurn | None:
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
        message = bound_text(event.message, capture)
        return ThreadTurn(
            provider=_CODEX_PROVIDER,
            provider_thread_id=lane.id,
            lane=lane.id,
            turn_id=event.turn_id,
            status="failed",
            failed_at=now,
            error=message.text if message is not None else None,
            completion_source="codex-event",
            updated_at=now,
        )
    return None


def _runtime_state(
    lane: Lane, event: LaneEvent, now: str, capture: CapturePolicy
) -> LaneRuntimeState | None:
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
        message = bound_text(event.message, capture)
        return _state(
            lane,
            now,
            status="error",
            latest_turn_id=event.turn_id,
            latest_turn_status="failed",
            needs_attention=True,
            attention_kind="turn_failed",
            attention_detail=message.text if message is not None else None,
        )
    if isinstance(event, LaneIdle):
        return _state(lane, now, status="idle")
    if isinstance(event, ThreadArchived):
        return _state(lane, now, status="archived")
    if isinstance(event, ThreadUnarchived):
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
