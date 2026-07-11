"""Normalize canonical Codex App Server thread items into provider-neutral rows."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.core.capture import (
    bound_payload,
    bound_redacted_json,
    bound_redacted_text,
)
from outfitter.dispatch.registry.models import ThreadItem, ThreadItemRef

CODEX_ITEM_TYPES = frozenset(
    {
        "userMessage",
        "hookPrompt",
        "agentMessage",
        "plan",
        "reasoning",
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "collabAgentToolCall",
        "subAgentActivity",
        "webSearch",
        "imageView",
        "sleep",
        "imageGeneration",
        "enteredReviewMode",
        "exitedReviewMode",
        "contextCompaction",
    }
)


def normalize_codex_item(
    raw_item: dict[str, object],
    *,
    provider_thread_id: str,
    lane: str,
    turn_id: str | None,
    inserted_at: str,
    position: int | None,
    capture: CapturePolicy,
    is_error: bool = False,
) -> tuple[ThreadItem, list[ThreadItemRef]]:
    """Return the shared normalized representation used by live and replay paths."""

    item_type = _string(raw_item.get("type")) or "unknown"
    item_id = _string(raw_item.get("id"))
    if item_id is None:
        raise ValueError("Codex thread item is missing a string id")
    status = _string(raw_item.get("status"))
    text = bound_redacted_text(_item_text(item_type, raw_item), capture)
    payload = _retained_payload(raw_item, capture, is_error=is_error or _has_error(raw_item))
    arguments = bound_redacted_json(raw_item.get("arguments"), capture)
    if not isinstance(arguments, dict | list | str | int | float | bool) and arguments is not None:
        arguments = str(arguments)
    item = ThreadItem(
        provider="codex",
        provider_thread_id=provider_thread_id,
        item_id=item_id,
        lane=lane,
        turn_id=turn_id,
        item_type=item_type,
        role=_role(item_type, raw_item),
        phase=_bounded_redacted(_string(raw_item.get("phase")), capture),
        status=_bounded_redacted(status, capture),
        text=text.text if text is not None else None,
        tool=_bounded_redacted(_tool(item_type, raw_item), capture),
        server=_bounded_redacted(_server(item_type, raw_item), capture),
        command=_bounded_redacted(_string(raw_item.get("command")), capture),
        cwd=_bounded_redacted(_string(raw_item.get("cwd")), capture),
        error=_bounded_redacted(_error_text(raw_item.get("error")), capture),
        duration_ms=_integer(raw_item.get("durationMs")),
        arguments=arguments,
        success=_success(item_type, raw_item, status),
        agent_nickname=_bounded_redacted(_string(raw_item.get("agentNickname")), capture),
        agent_role=_bounded_redacted(_string(raw_item.get("agentRole")), capture),
        created_at=_timestamp(raw_item),
        position=position,
        inserted_at=inserted_at,
        payload=payload,
        raw_retained=payload is not None,
    )
    return item, _item_refs(item, raw_item, capture)


def _role(item_type: str, item: dict[str, object]) -> str | None:
    explicit = _string(item.get("role"))
    if explicit is not None:
        return explicit
    return {"userMessage": "user", "agentMessage": "assistant"}.get(item_type)


def _tool(item_type: str, item: dict[str, object]) -> str | None:
    if item_type == "commandExecution":
        return "shell"
    if item_type in {"mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}:
        return _string(item.get("tool"))
    # Preserve compatibility with pre-canonical history fixtures.
    for key in ("toolName", "tool_name", "tool", "name"):
        value = _string(item.get(key))
        if value:
            return value
    return item_type if "tool" in item_type.casefold() else None


def _server(item_type: str, item: dict[str, object]) -> str | None:
    if item_type == "dynamicToolCall":
        return _string(item.get("namespace"))
    return _string(item.get("server"))


def _item_text(item_type: str, item: dict[str, object]) -> str | None:
    direct = _string(item.get("text"))
    if direct is not None:
        return direct
    if item_type in {"userMessage", "hookPrompt"}:
        return _join_text(
            item.get("content") if item_type == "userMessage" else item.get("fragments")
        )
    if item_type == "reasoning":
        return _join_strings(item.get("summary")) or _join_strings(item.get("content"))
    if item_type == "commandExecution":
        return _string(item.get("aggregatedOutput")) or _string(item.get("command"))
    if item_type == "fileChange":
        paths = sorted(_file_paths(item.get("changes")))
        return "\n".join(paths) or None
    if item_type in {"mcpToolCall", "dynamicToolCall"}:
        return (
            _json_text(item.get("error"))
            or _json_text(item.get("result"))
            or _join_text(item.get("contentItems"))
        )
    if item_type == "collabAgentToolCall":
        return _string(item.get("prompt"))
    if item_type == "subAgentActivity":
        return _string(item.get("kind"))
    if item_type == "webSearch":
        return _string(item.get("query"))
    if item_type == "imageView":
        return _string(item.get("path"))
    if item_type == "imageGeneration":
        return _string(item.get("result")) or _string(item.get("revisedPrompt"))
    if item_type in {"enteredReviewMode", "exitedReviewMode"}:
        return _string(item.get("review"))
    content = item.get("content")
    return _string(content) or _join_text(content)


def _item_refs(
    item: ThreadItem, raw_item: dict[str, object], policy: CapturePolicy
) -> list[ThreadItemRef]:
    refs: set[tuple[str, str]] = set()
    _add(refs, "tool", item.tool, policy)
    _add(refs, "tool_server", item.server, policy)
    _add(refs, "tool_status", item.status, policy)
    _add(refs, "command", item.command, policy)
    _add(refs, "cwd", item.cwd, policy)
    if item.error is not None:
        refs.add(("tool_error", "true"))
    raw_arguments = raw_item.get("arguments")
    if isinstance(raw_arguments, dict):
        for key in raw_arguments:
            _add(refs, "tool_arg_key", str(key), policy)
    for path in _file_paths(raw_item):
        _add(refs, "file", path, policy)
    for key, ref_type in (
        ("senderThreadId", "thread"),
        ("agentThreadId", "thread"),
        ("agentThreadId", "child_thread"),
        ("processId", "process"),
        ("clientId", "client"),
        ("model", "model"),
        ("reasoningEffort", "reasoning_effort"),
        ("agentPath", "agent_path"),
        ("kind", "agent_kind"),
    ):
        _add(refs, ref_type, _string(raw_item.get(key)), policy)
    receivers = raw_item.get("receiverThreadIds")
    if isinstance(receivers, list):
        for receiver in receivers:
            if isinstance(receiver, str):
                _add(refs, "thread", receiver, policy)
                _add(refs, "child_thread", receiver, policy)
    states = raw_item.get("agentsStates")
    if isinstance(states, dict):
        for thread_id in states:
            if isinstance(thread_id, str):
                _add(refs, "thread", thread_id, policy)
                _add(refs, "child_thread", thread_id, policy)
    fragments = raw_item.get("fragments")
    if isinstance(fragments, list):
        for fragment in fragments:
            if isinstance(fragment, dict):
                _add(refs, "hook_run", _string(fragment.get("hookRunId")), policy)
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


def _add(
    refs: set[tuple[str, str]], ref_type: str, value: str | None, policy: CapturePolicy
) -> None:
    bounded = _bounded_redacted(value, policy)
    if bounded:
        refs.add((ref_type, bounded))


def _file_paths(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"path", "file", "filePath", "file_path", "savedPath"} and isinstance(
                child, str
            ):
                found.add(child)
            else:
                found.update(_file_paths(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_file_paths(child))
    return found


def _join_text(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for part in value:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            text = _string(part.get("text"))
            if text:
                parts.append(text)
            elif isinstance(part.get("path"), str):
                parts.append(str(part["path"]))
            elif isinstance(part.get("url"), str):
                parts.append(str(part["url"]))
    return "\n".join(parts) or None


def _join_strings(value: object) -> str | None:
    if not isinstance(value, Iterable) or isinstance(value, str | bytes | dict):
        return None
    parts = [part for part in value if isinstance(part, str)]
    return "\n".join(parts) or None


def _json_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("message", "content", "text"):
            found = _string(value.get(key))
            if found:
                return found
    return None


def _error_text(value: object) -> str | None:
    return _json_text(value)


def _bounded_redacted(value: str | None, policy: CapturePolicy) -> str | None:
    bounded = bound_redacted_text(value, policy)
    return bounded.text if bounded is not None else None


def _has_error(item: dict[str, object]) -> bool:
    return item.get("error") is not None or _string(item.get("status")) in {"failed", "error"}


def _success(item_type: str, item: dict[str, object], status: str | None) -> bool | None:
    explicit = item.get("success")
    if isinstance(explicit, bool):
        return explicit
    exit_code = item.get("exitCode")
    if item_type == "commandExecution" and isinstance(exit_code, int):
        return exit_code == 0
    if status in {"completed", "success"}:
        return True
    if status in {"failed", "error", "declined"}:
        return False
    return None


def _timestamp(value: dict[str, object]) -> str | None:
    for key in ("createdAt", "created_at", "timestamp"):
        found = _string(value.get(key))
        if found:
            return found
    return None


def _retained_payload(
    item: dict[str, object], capture: CapturePolicy, *, is_error: bool
) -> dict[str, object] | None:
    if not capture.should_retain_raw_payload(is_error=is_error):
        return None
    return bound_payload(_json_safe_dict(item), capture).payload


def _json_safe_dict(value: dict[str, object]) -> dict[str, object]:
    return {str(key): _json_safe(child) for key, child in value.items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_safe(child) for child in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
