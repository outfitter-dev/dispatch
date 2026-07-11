"""Transcript history summarization and filtering helpers."""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.registry.models import Lane, LaneSync, ThreadItem, ThreadItemRef

from .codex_items import normalize_codex_item
from .models import (
    HistoryFileStat,
    HistoryItem,
    HistoryThreadSummary,
    HistoryToolStat,
    HistoryWorktree,
)


def history_items_from_thread(
    result: dict[str, object],
    *,
    item_type: str | None = None,
    role: str | None = None,
    phase: str | None = None,
    tool: str | None = None,
    tool_server: str | None = None,
    tool_status: str | None = None,
    errored: bool | None = None,
    mentions_thread: str | None = None,
    arg_key: str | None = None,
    grep: str | None = None,
    raw: bool = False,
    limit: int = 50,
) -> list[HistoryItem]:
    items = _all_history_items(result, raw=raw)
    filtered = [
        item
        for item in items
        if _matches_filter(
            item,
            item_type=item_type,
            role=role,
            phase=phase,
            tool=tool,
            tool_server=tool_server,
            tool_status=tool_status,
            errored=errored,
            mentions_thread=mentions_thread,
            arg_key=arg_key,
            grep=grep,
        )
    ]
    return filtered[-limit:]


def history_items_from_indexed(
    items: list[ThreadItem],
    refs_by_item: dict[tuple[str, str, str], list[ThreadItemRef]],
    *,
    item_type: str | None = None,
    role: str | None = None,
    phase: str | None = None,
    tool: str | None = None,
    tool_server: str | None = None,
    tool_status: str | None = None,
    errored: bool | None = None,
    mentions_thread: str | None = None,
    arg_key: str | None = None,
    grep: str | None = None,
    raw: bool = False,
    limit: int = 50,
) -> list[HistoryItem]:
    """Project normalized DB-backed history rows into user-facing history items."""

    projected = [
        _indexed_history_item(item, refs_by_item.get(_item_identity(item), []), include_raw=raw)
        for item in reversed(items)
    ]
    filtered = [
        item
        for item in projected
        if _matches_filter(
            item,
            item_type=item_type,
            role=role,
            phase=phase,
            tool=tool,
            tool_server=tool_server,
            tool_status=tool_status,
            errored=errored,
            mentions_thread=mentions_thread,
            arg_key=arg_key,
            grep=grep,
        )
    ]
    return filtered[-limit:]


def history_rollups_from_indexed(
    items: list[ThreadItem],
    refs_by_item: dict[tuple[str, str, str], list[ThreadItemRef]],
) -> tuple[list[HistoryToolStat], list[HistoryFileStat]]:
    projected = [
        _indexed_history_item(item, refs_by_item.get(_item_identity(item), []), include_raw=False)
        for item in reversed(items)
    ]
    return _history_rollups(projected)


def summarize_history(
    result: dict[str, object],
    *,
    lane: Lane,
    sync: LaneSync | None,
    worktree: HistoryWorktree | None = None,
) -> tuple[HistoryThreadSummary, list[HistoryItem], list[HistoryToolStat], list[HistoryFileStat]]:
    items = _all_history_items(result, raw=True)
    thread = result.get("thread")
    turns = _turns(thread if isinstance(thread, dict) else {})
    transcript_bytes = (
        len(json.dumps(thread, separators=(",", ":"))) if isinstance(thread, dict) else None
    )
    tools, files = _history_rollups(items)
    subagent_ids = sorted(
        {value for item in items for value in _subagent_thread_ids(item.raw or {})}
    )
    summary = HistoryThreadSummary(
        ref=lane.ref,
        id=lane.id,
        handle=lane.handle,
        source=lane.source,
        status=lane.status,
        cwd=lane.cwd,
        first_event_at=_first_event_at(thread if isinstance(thread, dict) else {}),
        last_event_at=sync.latest_event_at if sync is not None else None,
        turns=len(turns),
        items=len(items),
        messages=sum(1 for item in items if _is_message(item)),
        tool_calls=sum(tool.count for tool in tools),
        unique_tools=sorted(tool.tool for tool in tools),
        files_changed_count=len(files),
        files_changed=files[:25],
        transcript_bytes=transcript_bytes,
        estimated_tokens=(transcript_bytes // 4) if transcript_bytes is not None else None,
        subagents_count=len(subagent_ids),
        subagent_thread_ids=subagent_ids,
        worktree=worktree or HistoryWorktree(),
    )
    return summary, items, tools, files


def _history_rollups(
    items: list[HistoryItem],
) -> tuple[list[HistoryToolStat], list[HistoryFileStat]]:
    tool_counter: Counter[str] = Counter(item.tool for item in items if item.tool)
    item_types_by_tool: dict[str, set[str]] = defaultdict(set)
    for item in items:
        if item.tool:
            item_types_by_tool[item.tool].add(item.type)
    file_counter: Counter[str] = Counter()
    for item in items:
        file_counter.update(item.files)
    tools = [
        HistoryToolStat(
            tool=name,
            count=count,
            item_types=sorted(item_types_by_tool[name]),
        )
        for name, count in tool_counter.most_common()
    ]
    files = [HistoryFileStat(path=path, count=count) for path, count in file_counter.most_common()]
    return tools, files


async def detect_worktree(cwd: str | None) -> HistoryWorktree:
    if cwd is None:
        return HistoryWorktree()
    return await _detect_worktree(cwd)


async def _detect_worktree(cwd: str) -> HistoryWorktree:
    import asyncio

    return await asyncio.to_thread(_detect_worktree_sync, cwd)


def _detect_worktree_sync(cwd: str) -> HistoryWorktree:
    path = Path(cwd).expanduser()
    if not path.exists():
        return HistoryWorktree(path=str(path), is_codex_worktree=_looks_like_codex_worktree(path))
    repo = _git(cwd, "rev-parse", "--show-toplevel")
    branch = _git(cwd, "branch", "--show-current")
    head = _git(cwd, "rev-parse", "--short", "HEAD")
    common_dir = _git(cwd, "rev-parse", "--git-common-dir")
    changed_files = _changed_files(cwd)
    detected = bool(repo and common_dir and Path(common_dir).name == "worktrees")
    return HistoryWorktree(
        detected=detected,
        path=str(path),
        repo=repo,
        branch=branch,
        head=head,
        dirty=bool(changed_files),
        changed_files_count=len(changed_files),
        changed_files=changed_files[:50],
        is_codex_worktree=_looks_like_codex_worktree(path),
    )


def _git(cwd: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ("git", "-C", cwd, *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _changed_files(cwd: str) -> list[str]:
    raw = _git(cwd, "status", "--porcelain=v1", "-uno")
    if raw is None:
        return []
    files: list[str] = []
    for line in raw.splitlines():
        if not line:
            continue
        path = line[2:].strip()
        if " -> " in path:
            _old, _arrow, path = path.partition(" -> ")
        if path:
            files.append(path)
    return sorted(set(files))


def _looks_like_codex_worktree(path: Path) -> bool:
    raw = str(path)
    return "/.config/codex/worktrees/" in raw or "/.codex/worktrees/" in raw


def _all_history_items(result: dict[str, object], *, raw: bool) -> list[HistoryItem]:
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return []
    items: list[HistoryItem] = []
    for turn in _turns(thread):
        turn_id = _string(turn.get("id"))
        raw_items = turn.get("items")
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            items.append(_history_item(turn_id, raw_item, include_raw=raw))
    return items


def _history_item(
    turn_id: str | None, item: dict[str, object], *, include_raw: bool
) -> HistoryItem:
    item_id = _string(item.get("id")) or "raw-history-item"
    normalized, refs = normalize_codex_item(
        item if item.get("id") is not None else {**item, "id": item_id},
        provider_thread_id="raw-history",
        lane="raw-history",
        turn_id=turn_id,
        inserted_at="",
        position=None,
        capture=CapturePolicy(),
    )
    projected = _indexed_history_item(normalized, refs, include_raw=False)
    return projected.model_copy(update={"raw": dict(item) if include_raw else None})


def _indexed_history_item(
    item: ThreadItem, refs: list[ThreadItemRef], *, include_raw: bool
) -> HistoryItem:
    return HistoryItem(
        turn_id=item.turn_id,
        item_id=item.item_id,
        type=item.item_type,
        text=item.text,
        role=item.role,
        phase=item.phase,
        tool=item.tool,
        tool_server=item.server,
        tool_status=item.status,
        command=item.command,
        command_cwd=item.cwd,
        arguments=item.arguments,
        success=item.success,
        error=item.error,
        errored=item.error is not None or item.success is False,
        duration_ms=item.duration_ms,
        agent_nickname=item.agent_nickname,
        agent_role=item.agent_role,
        thread_ids=sorted(ref.ref_value for ref in refs if ref.ref_type == "thread"),
        child_thread_ids=sorted(ref.ref_value for ref in refs if ref.ref_type == "child_thread"),
        argument_keys=sorted(ref.ref_value for ref in refs if ref.ref_type == "tool_arg_key"),
        files=sorted(ref.ref_value for ref in refs if ref.ref_type == "file"),
        raw=item.payload if include_raw and item.raw_retained else None,
    )


def _item_identity(item: ThreadItem) -> tuple[str, str, str]:
    return (item.provider, item.provider_thread_id, item.item_id)


def _turns(thread: dict[str, object]) -> list[dict[str, object]]:
    raw_turns = thread.get("turns")
    if not isinstance(raw_turns, list):
        return []
    return [turn for turn in raw_turns if isinstance(turn, dict)]


def _matches_filter(
    item: HistoryItem,
    *,
    item_type: str | None,
    role: str | None,
    phase: str | None,
    tool: str | None,
    tool_server: str | None,
    tool_status: str | None,
    errored: bool | None,
    mentions_thread: str | None,
    arg_key: str | None,
    grep: str | None,
) -> bool:
    if item_type is not None and item_type.casefold() not in item.type.casefold():
        return False
    if tool is not None and (item.tool is None or tool.casefold() not in item.tool.casefold()):
        return False
    if role is not None and (item.role is None or role.casefold() not in item.role.casefold()):
        return False
    if phase is not None and (item.phase is None or phase.casefold() not in item.phase.casefold()):
        return False
    if tool_server is not None and (
        item.tool_server is None or tool_server.casefold() not in item.tool_server.casefold()
    ):
        return False
    if tool_status is not None and (
        item.tool_status is None or tool_status.casefold() not in item.tool_status.casefold()
    ):
        return False
    if errored is not None and item.errored != errored:
        return False
    if mentions_thread is not None and not any(
        mentions_thread.casefold() in thread_id.casefold() for thread_id in item.thread_ids
    ):
        return False
    if arg_key is not None and arg_key not in item.argument_keys:
        return False
    return not (grep is not None and grep.casefold() not in (item.text or "").casefold())


def _is_message(item: HistoryItem) -> bool:
    return "message" in item.type.casefold() or item.role in {"user", "assistant", "system"}


def _subagent_thread_ids(item: dict[str, object]) -> list[str]:
    blob = json.dumps(item, separators=(",", ":"))
    return re.findall(r"019[a-z0-9-]{28,}", blob)


def _first_event_at(thread: dict[str, object]) -> str | None:
    timestamps: list[str] = []
    for turn in _turns(thread):
        for key in ("createdAt", "created_at", "timestamp"):
            value = _string(turn.get(key))
            if value:
                timestamps.append(value)
        raw_items = turn.get("items")
        if isinstance(raw_items, list):
            for item in raw_items:
                if isinstance(item, dict):
                    value = _string(item.get("createdAt")) or _string(item.get("timestamp"))
                    if value:
                        timestamps.append(value)
    return min(timestamps) if timestamps else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
