"""Index App Server thread/read history into normalized registry tables."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.core.capture import bound_payload, bound_text
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
            seen_item_ids.add(item_id)
            item_text = bound_text(_item_text(raw_item), policy)
            payload = _retained_payload(raw_item, policy, is_error=_is_error_item(raw_item, status))
            item = ThreadItem(
                provider=_CODEX_PROVIDER,
                provider_thread_id=provider_thread_id,
                item_id=item_id,
                lane=lane.id,
                turn_id=turn_id,
                item_type=_string(raw_item.get("type")) or "unknown",
                role=_string(raw_item.get("role")),
                text=item_text.text if item_text is not None else None,
                tool=_tool_name(raw_item),
                created_at=_timestamp(raw_item),
                position=position,
                inserted_at=now,
                payload=payload,
                raw_retained=payload is not None,
            )
            indexed_items.append((item, _item_refs(item, raw_item)))
            position += 1
    await registry.upsert_thread_history_snapshot(
        turns=indexed_turns,
        items=indexed_items,
        provider=_CODEX_PROVIDER,
        provider_thread_id=provider_thread_id,
        turn_ids=seen_turn_ids,
        item_ids=seen_item_ids,
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


def _item_text(item: dict[str, object]) -> str | None:
    direct = item.get("text")
    if isinstance(direct, str):
        return direct
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return None


def _tool_name(item: dict[str, object]) -> str | None:
    for key in ("toolName", "tool_name", "tool", "name", "command"):
        value = _string(item.get(key))
        if value:
            return value
    item_type = _string(item.get("type")) or ""
    if "tool" in item_type.casefold():
        return item_type
    return None


def _item_refs(item: ThreadItem, raw_item: dict[str, object]) -> list[ThreadItemRef]:
    refs: set[tuple[str, str]] = set()
    if item.tool:
        refs.add(("tool", item.tool))
    for key, ref_type in (
        ("server", "tool_server"),
        ("status", "tool_status"),
    ):
        value = _string(raw_item.get(key))
        if value:
            refs.add((ref_type, value))
    if raw_item.get("error") is not None:
        refs.add(("tool_error", "true"))
    arguments = raw_item.get("arguments")
    if isinstance(arguments, dict):
        for key in arguments:
            if isinstance(key, str):
                refs.add(("tool_arg_key", key))
    for path in _file_paths(raw_item):
        refs.add(("file", path))
    blob = json.dumps(raw_item, separators=(",", ":"))
    for thread_id in re.findall(r"019[a-z0-9-]{28,}", blob):
        refs.add(("thread", thread_id))
    return [
        ThreadItemRef(
            provider=item.provider,
            provider_thread_id=item.provider_thread_id,
            item_id=item.item_id,
            ref_type=ref_type,
            ref_value=ref_value,
        )
        for ref_type, ref_value in sorted(refs)
    ]


def _file_paths(value: object) -> list[str]:
    found: list[str] = []
    _collect_paths(value, found)
    return sorted(set(found))


def _collect_paths(value: object, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"path", "file", "filePath", "file_path"} and isinstance(child, str):
                found.append(child)
            else:
                _collect_paths(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_paths(child, found)


def _compact_payload(item: dict[str, object]) -> dict[str, object]:
    return {key: _json_safe(value) for key, value in item.items()}


def _retained_payload(
    item: dict[str, object], policy: CapturePolicy, *, is_error: bool
) -> dict[str, object] | None:
    if not policy.should_retain_raw_payload(is_error=is_error):
        return None
    return bound_payload(_compact_payload(item), policy).payload


def _is_error_item(
    item: dict[str, object], turn_status: Literal["started", "completed", "failed", "unknown"]
) -> bool:
    if turn_status == "failed":
        return True
    if item.get("error") is not None:
        return True
    item_type = _string(item.get("type"))
    return item_type is not None and "error" in item_type.casefold()


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_safe(child) for child in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
