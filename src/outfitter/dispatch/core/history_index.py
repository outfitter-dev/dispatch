"""Index App Server thread/read history into normalized registry tables."""

from __future__ import annotations

from typing import Any, Literal

from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.core.capture import bound_text
from outfitter.dispatch.core.codex_items import normalize_codex_item
from outfitter.dispatch.registry.models import Lane, ThreadItem, ThreadItemRef, ThreadTurn
from outfitter.dispatch.registry.store import Registry

_CODEX_PROVIDER = "codex"
_TURN_STATUSES: set[str] = {"started", "completed", "failed", "unknown"}


async def index_codex_thread_read(
    registry: Registry,
    lane: Lane,
    result: dict[str, object],
    capture: CapturePolicy | None = None,
) -> None:
    """Backfill normalized turn/item rows from a Codex thread/read payload."""

    policy = capture or CapturePolicy()
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return
    now = registry.now_iso()
    provider_thread_id = _string(thread.get("id")) or lane.id
    position = 0
    seen_turn_ids: set[str] = set()
    seen_item_ids: set[str] = set()
    indexed_turns: list[ThreadTurn] = []
    indexed_items: list[tuple[ThreadItem, list[ThreadItemRef]]] = []
    for turn_index, raw_turn in enumerate(turns):
        if not isinstance(raw_turn, dict):
            continue
        turn_id = _string(raw_turn.get("id")) or f"turn-{turn_index}"
        seen_turn_ids.add(turn_id)
        status = _turn_status(raw_turn.get("status"))
        created_at = _timestamp(raw_turn)
        turn_error = bound_text(_string(raw_turn.get("error")), policy)
        indexed_turns.append(
            ThreadTurn(
                provider=_CODEX_PROVIDER,
                provider_thread_id=provider_thread_id,
                lane=lane.id,
                turn_id=turn_id,
                status=status,
                started_at=created_at,
                completed_at=now if status == "completed" else None,
                failed_at=now if status == "failed" else None,
                error=turn_error.text if turn_error is not None else None,
                completion_source="thread-read" if status != "unknown" else None,
                updated_at=now,
            )
        )
        raw_items = raw_turn.get("items")
        if not isinstance(raw_items, list):
            continue
        if policy.mode == "minimal":
            continue
        for item_index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, dict):
                continue
            item_id = _string(raw_item.get("id")) or f"{turn_id}:item-{item_index}"
            if raw_item.get("id") is None:
                raw_item = {**raw_item, "id": item_id}
            seen_item_ids.add(item_id)
            item, refs = normalize_codex_item(
                raw_item,
                provider_thread_id=provider_thread_id,
                lane=lane.id,
                turn_id=turn_id,
                inserted_at=now,
                position=position,
                capture=policy,
                is_error=_is_error_item(raw_item, status),
            )
            indexed_items.append((item, refs))
            position += 1
    if policy.mode == "minimal":
        for turn in indexed_turns:
            await registry.upsert_thread_turn(turn)
        return
    await registry.upsert_thread_history_snapshot(
        turns=indexed_turns,
        items=indexed_items,
        provider=_CODEX_PROVIDER,
        provider_thread_id=provider_thread_id,
        turn_ids=seen_turn_ids,
        item_ids=seen_item_ids,
        prune_missing=False,
    )


def _turn_status(value: object) -> Literal["started", "completed", "failed", "unknown"]:
    if isinstance(value, str) and value in _TURN_STATUSES:
        return value  # type: ignore[return-value]
    return "unknown"


def _timestamp(value: dict[str, object]) -> str | None:
    for key in ("createdAt", "created_at", "timestamp"):
        found = _string(value.get(key))
        if found:
            return found
    return None


def _is_error_item(
    item: dict[str, object], turn_status: Literal["started", "completed", "failed", "unknown"]
) -> bool:
    if turn_status == "failed":
        return True
    if item.get("error") is not None:
        return True
    item_type = _string(item.get("type"))
    return item_type is not None and "error" in item_type.casefold()


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
