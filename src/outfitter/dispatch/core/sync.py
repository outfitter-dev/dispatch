"""Codex thread source parsing for progressive lane sync.

Sync indexes compact facts from Codex's persisted JSONL artifacts. It does not
copy transcripts wholesale; App Server history reads remain the semantic source
for transcript commands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SyncScanState = Literal["partial", "complete", "error"]


@dataclass(frozen=True)
class SyncLimits:
    top_bytes: int = 262_144
    tail_bytes: int = 262_144
    tail_lines: int = 200
    full_bytes: int = 16_777_216


DEFAULT_SYNC_LIMITS = SyncLimits()


@dataclass(frozen=True)
class SourceIdentity:
    path: str
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class JsonlSyncFacts:
    state: SyncScanState
    source: SourceIdentity | None
    line_count: int | None = None
    first_offset: int | None = None
    tail_offset: int | None = None
    next_offset: int | None = None
    latest_event_at: str | None = None
    latest_turn_id: str | None = None
    session_id: str | None = None
    cwd: str | None = None
    source_kind: str | None = None
    thread_source: str | None = None
    model_provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    error: str | None = None
    stable_during_read: bool = True
    bytes_scanned: int = 0
    unchanged: bool = False


def scan_codex_jsonl(
    path: str,
    *,
    full: bool = False,
    limits: SyncLimits = DEFAULT_SYNC_LIMITS,
    previous: SourceIdentity | None = None,
    previous_offset: int | None = None,
) -> JsonlSyncFacts:
    source_path = Path(path).expanduser()
    records: list[dict[str, Any]]
    line_count: int | None
    first_offset: int | None
    tail_offset: int | None
    state: SyncScanState
    next_offset: int | None
    try:
        before = source_path.stat()
    except OSError as exc:
        return JsonlSyncFacts(state="error", source=None, error=f"stat failed: {exc}")

    source = SourceIdentity(
        path=str(source_path),
        device=before.st_dev,
        inode=before.st_ino,
        size=before.st_size,
        mtime_ns=before.st_mtime_ns,
    )
    fully_consumed = source.size == 0 or (
        previous_offset is not None and previous_offset >= source.size
    )
    if previous == source and fully_consumed:
        return JsonlSyncFacts(
            state="partial",
            source=source,
            stable_during_read=True,
            bytes_scanned=0,
            unchanged=True,
            next_offset=previous_offset,
        )
    try:
        same_growing_source = (
            previous is not None
            and previous.path == source.path
            and previous.device == source.device
            and previous.inode == source.inode
            and previous.size <= source.size
            and previous_offset is not None
            and previous_offset < source.size
        )
        if same_growing_source and previous_offset is not None:
            records, tail_offset, next_offset, bytes_scanned, blocked_record = _read_records_from(
                source_path, previous_offset, limits.tail_bytes
            )
            line_count = None
            first_offset = previous_offset
            state = "partial"
            scan_error = (
                f"complete JSONL record at offset {previous_offset} exceeds the "
                f"{limits.tail_bytes}-byte local scan budget; rerun with larger --max-bytes"
                if blocked_record
                else None
            )
        elif full:
            records, line_count, first_offset, tail_offset, bytes_scanned = _read_all_records(
                source_path, limits.full_bytes
            )
            state = "complete" if bytes_scanned >= source.size else "partial"
            next_offset = bytes_scanned
            scan_error = None
        else:
            records, line_count, first_offset, tail_offset, next_offset = _read_quick_records(
                source_path, limits
            )
            state = "partial"
            bytes_scanned = min(source.size, limits.top_bytes) + min(source.size, limits.tail_bytes)
            scan_error = None
    except OSError as exc:
        return JsonlSyncFacts(state="error", source=source, error=f"read failed: {exc}")

    summary = _summarize_records(records)
    error: str | None = scan_error
    try:
        after = source_path.stat()
        stable_during_read = (
            before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
        )
    except OSError as exc:
        state = "error"
        error = f"post-read stat failed: {exc}"
        stable_during_read = False
    return JsonlSyncFacts(
        state=state,
        source=source,
        line_count=line_count,
        first_offset=first_offset,
        tail_offset=tail_offset,
        next_offset=next_offset,
        latest_event_at=summary.latest_event_at,
        latest_turn_id=summary.latest_turn_id,
        session_id=summary.session_id,
        cwd=summary.cwd,
        source_kind=summary.source_kind,
        thread_source=summary.thread_source,
        model_provider=summary.model_provider,
        model=summary.model,
        reasoning_effort=summary.reasoning_effort,
        error=error,
        stable_during_read=stable_during_read,
        bytes_scanned=min(bytes_scanned, source.size),
    )


def _read_all_records(
    path: Path, max_bytes: int
) -> tuple[list[dict[str, Any]], int, int | None, int | None, int]:
    records: list[dict[str, Any]] = []
    first_offset: int | None = None
    tail_offset: int | None = None
    offset = 0
    line_count = 0
    with path.open("rb") as handle:
        for raw in handle:
            if offset + len(raw) > max_bytes:
                break
            line_count += 1
            if first_offset is None:
                first_offset = offset
            parsed = _parse_line(raw)
            if parsed is not None:
                records.append(parsed)
                tail_offset = offset
            offset += len(raw)
    return records, line_count, first_offset, tail_offset, offset


def _read_quick_records(
    path: Path, limits: SyncLimits
) -> tuple[list[dict[str, Any]], None, int | None, int | None, int | None]:
    top_records, first_offset = _read_top_records(path, limits.top_bytes)
    tail_records, tail_offset, next_offset = _read_tail_records(
        path, limits.tail_bytes, limits.tail_lines
    )
    seen: set[int] = set()
    records: list[dict[str, Any]] = []
    for offset, record in (*top_records, *tail_records):
        if offset in seen:
            continue
        seen.add(offset)
        records.append(record)
    return records, None, first_offset, tail_offset, next_offset


def _read_top_records(
    path: Path, max_bytes: int
) -> tuple[list[tuple[int, dict[str, Any]]], int | None]:
    if max_bytes <= 0:
        return [], None
    with path.open("rb") as handle:
        data = handle.read(max_bytes)

    lines = data.splitlines(keepends=True)
    if lines and not lines[-1].endswith(b"\n"):
        lines = lines[:-1]

    records: list[tuple[int, dict[str, Any]]] = []
    offset = 0
    first_offset: int | None = None
    for raw in lines:
        parsed = _parse_line(raw)
        if parsed is not None:
            if first_offset is None:
                first_offset = offset
            records.append((offset, parsed))
        offset += len(raw)
    return records, first_offset


def _read_tail_records(
    path: Path, max_bytes: int, max_lines: int
) -> tuple[list[tuple[int, dict[str, Any]]], int | None, int | None]:
    if max_bytes <= 0 or max_lines <= 0:
        return [], None, None
    size = path.stat().st_size
    read_size = min(size, max_bytes)
    with path.open("rb") as handle:
        handle.seek(size - read_size)
        data = handle.read(read_size)

    lines = data.splitlines(keepends=True)
    offset = size - len(data)
    if read_size < size and lines:
        offset += len(lines[0])
        lines = lines[1:]
    if lines and not lines[-1].endswith(b"\n"):
        lines = lines[:-1]

    selected = lines[-max_lines:]
    offset += sum(len(line) for line in lines[: len(lines) - len(selected)])
    records: list[tuple[int, dict[str, Any]]] = []
    first_offset: int | None = None
    next_offset: int | None = offset
    for raw in selected:
        parsed = _parse_line(raw)
        if parsed is not None:
            if first_offset is None:
                first_offset = offset
            records.append((offset, parsed))
        offset += len(raw)
        next_offset = offset
    return records, first_offset, next_offset


def _read_records_from(
    path: Path, offset: int, max_bytes: int
) -> tuple[list[dict[str, Any]], int | None, int, int, bool]:
    records: list[dict[str, Any]] = []
    tail_offset: int | None = None
    next_offset = offset
    bytes_scanned = 0
    blocked_record = False
    with path.open("rb") as handle:
        handle.seek(offset)
        while bytes_scanned < max_bytes:
            raw = handle.readline(max_bytes - bytes_scanned + 1)
            if not raw:
                break
            if bytes_scanned + len(raw) > max_bytes:
                blocked_record = True
                break
            if not raw.endswith(b"\n"):
                break
            parsed = _parse_line(raw)
            if parsed is not None:
                records.append(parsed)
                tail_offset = next_offset
            bytes_scanned += len(raw)
            next_offset += len(raw)
    return records, tail_offset, next_offset, bytes_scanned, blocked_record


def _parse_line(raw: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class _Summary:
    latest_event_at: str | None = None
    latest_turn_id: str | None = None
    session_id: str | None = None
    cwd: str | None = None
    source_kind: str | None = None
    thread_source: str | None = None
    model_provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None


def _summarize_records(records: list[dict[str, Any]]) -> _Summary:
    summary = _Summary()
    for record in records:
        timestamp = _string(record.get("timestamp"))
        if timestamp is not None:
            summary.latest_event_at = timestamp
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if record.get("type") == "session_meta":
            summary.session_id = _string(payload.get("id")) or summary.session_id
            summary.cwd = _string(payload.get("cwd")) or summary.cwd
            summary.source_kind = _string(payload.get("source")) or summary.source_kind
            summary.thread_source = _string(payload.get("thread_source")) or summary.thread_source
            summary.model_provider = (
                _string(payload.get("model_provider")) or summary.model_provider
            )
            continue
        if record.get("type") == "turn_context":
            summary.cwd = _string(payload.get("cwd")) or summary.cwd
            summary.model = _string(payload.get("model")) or summary.model
            summary.reasoning_effort = _string(payload.get("effort")) or summary.reasoning_effort
            continue
        payload_type = payload.get("type")
        if payload_type == "task_complete":
            summary.latest_turn_id = _string(payload.get("turn_id")) or summary.latest_turn_id
    return summary


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None
