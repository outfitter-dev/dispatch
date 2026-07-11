"""Codex JSONL sync parser tests."""

from __future__ import annotations

import json
from pathlib import Path

from outfitter.dispatch.core.sync import SyncLimits, scan_codex_jsonl


def _write_jsonl(
    path: Path, records: list[dict[str, object]], *, partial: str | None = None
) -> None:
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
        if partial is not None:
            handle.write(partial)


def test_quick_scan_reads_bounded_top_and_tail_without_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    large_context = "x" * 5000
    _write_jsonl(
        path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-06-05T10:00:00.000Z",
                "payload": {
                    "id": "T1",
                    "cwd": "/work",
                    "source": "vscode",
                    "thread_source": "user",
                    "model_provider": "openai",
                    "base_instructions": large_context,
                },
            },
            {
                "type": "turn_context",
                "timestamp": "2026-06-05T10:00:01.000Z",
                "payload": {"cwd": "/work", "model": "test-model", "effort": "low"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-05T10:00:02.000Z",
                "payload": {"type": "token_count"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-05T10:00:03.000Z",
                "payload": {"type": "task_complete", "turn_id": "turn-1"},
            },
        ],
        partial='{"type": "event_msg", "timestamp"',
    )

    facts = scan_codex_jsonl(
        str(path),
        limits=SyncLimits(top_bytes=10_000, tail_bytes=512, tail_lines=2),
    )

    assert facts.state == "partial"
    assert facts.source is not None
    assert facts.source.size == path.stat().st_size
    assert facts.line_count is None
    assert facts.first_offset == 0
    assert facts.tail_offset is not None
    assert facts.session_id == "T1"
    assert facts.cwd == "/work"
    assert facts.source_kind == "vscode"
    assert facts.thread_source == "user"
    assert facts.model_provider == "openai"
    assert facts.model == "test-model"
    assert facts.reasoning_effort == "low"
    assert facts.latest_turn_id == "turn-1"
    assert facts.latest_event_at == "2026-06-05T10:00:03.000Z"


def test_quick_scan_honors_top_byte_limit_for_large_first_line(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-06-05T10:00:00.000Z",
                "payload": {"id": "T1", "base_instructions": "x" * 20_000},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-05T10:00:03.000Z",
                "payload": {"type": "task_complete", "turn_id": "turn-1"},
            },
        ],
    )

    facts = scan_codex_jsonl(
        str(path),
        limits=SyncLimits(top_bytes=128, tail_bytes=512, tail_lines=1),
    )

    assert facts.state == "partial"
    assert facts.first_offset is None
    assert facts.session_id is None
    assert facts.latest_turn_id == "turn-1"


def test_full_scan_marks_complete_and_reports_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-06-05T10:00:00.000Z",
                "payload": {"id": "T1"},
            }
        ],
    )

    full = scan_codex_jsonl(str(path), full=True)
    missing = scan_codex_jsonl(str(tmp_path / "missing.jsonl"))

    assert full.state == "complete"
    assert full.line_count == 1
    assert missing.state == "error"
    assert missing.error is not None


def test_scan_skips_unchanged_source_and_bounds_full_reads(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "T1", "value": "x" * 256}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ],
    )

    first = scan_codex_jsonl(str(path), full=True, limits=SyncLimits(full_bytes=128))
    assert first.state == "partial"
    assert first.bytes_scanned <= 128
    assert first.source is not None

    unchanged = scan_codex_jsonl(
        str(path), previous=first.source, previous_offset=path.stat().st_size
    )
    assert unchanged.unchanged is True
    assert unchanged.bytes_scanned == 0


def test_explicit_full_scan_rescans_partial_source_from_byte_zero(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "T1", "part": 1}},
            {"type": "event_msg", "payload": {"type": "task_complete", "part": 2}},
        ],
    )
    limits = SyncLimits(full_bytes=80, tail_bytes=512)
    first = scan_codex_jsonl(str(path), full=True, limits=limits)
    assert first.state == "partial"
    assert first.source is not None
    assert first.next_offset is not None
    assert first.next_offset < first.source.size

    second = scan_codex_jsonl(
        str(path),
        full=True,
        limits=limits,
        previous=first.source,
        previous_offset=first.next_offset,
    )

    assert second.unchanged is False
    assert second.bytes_scanned > 0
    assert second.first_offset == 0
    assert second.next_offset == first.next_offset


def test_explicit_full_scan_rescans_unchanged_quick_scan_at_eof(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "T1"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1"}},
        ],
    )

    quick = scan_codex_jsonl(str(path))
    assert quick.state == "partial"
    assert quick.source is not None
    assert quick.next_offset == path.stat().st_size

    full = scan_codex_jsonl(
        str(path),
        full=True,
        previous=quick.source,
        previous_offset=quick.next_offset,
    )

    assert full.state == "complete"
    assert full.unchanged is False
    assert full.first_offset == 0
    assert full.next_offset == path.stat().st_size
    assert full.line_count == 2
    assert full.session_id == "T1"
    assert full.latest_turn_id == "turn-1"


def test_complete_full_scan_still_allows_ordinary_unchanged_skip(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(path, [{"type": "session_meta", "payload": {"id": "T1"}}])

    full = scan_codex_jsonl(str(path), full=True)
    assert full.state == "complete"
    assert full.source is not None

    unchanged = scan_codex_jsonl(
        str(path),
        previous=full.source,
        previous_offset=full.next_offset,
    )

    assert unchanged.unchanged is True
    assert unchanged.bytes_scanned == 0


def test_scan_retries_same_identity_when_prior_scan_never_started(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [{"type": "session_meta", "payload": {"id": "T1"}}],
    )
    first = scan_codex_jsonl(str(path), limits=SyncLimits(top_bytes=0, tail_bytes=0))
    assert first.source is not None
    assert first.next_offset is None

    second = scan_codex_jsonl(
        str(path),
        previous=first.source,
        previous_offset=first.next_offset,
        limits=SyncLimits(top_bytes=1024, tail_bytes=1024),
    )

    assert second.unchanged is False
    assert second.session_id == "T1"


def test_scan_reports_oversized_complete_record_without_advancing(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "T1", "large": "x" * 500}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ],
    )
    source = scan_codex_jsonl(str(path), full=True, limits=SyncLimits(full_bytes=0)).source
    assert source is not None

    result = scan_codex_jsonl(
        str(path),
        previous=source,
        previous_offset=0,
        limits=SyncLimits(tail_bytes=64),
    )

    assert result.next_offset == 0
    assert result.bytes_scanned == 0
    assert result.error is not None
    assert "exceeds the 64-byte local scan budget" in result.error


def test_scan_continues_from_last_complete_line_after_append(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [{"type": "session_meta", "payload": {"id": "T1"}}],
        partial='{"type":"event_msg"',
    )
    first = scan_codex_jsonl(str(path))
    assert first.source is not None
    assert first.next_offset is not None

    with path.open("a") as handle:
        handle.write(',"payload":{"type":"task_complete","turn_id":"turn-2"}}\n')
    second = scan_codex_jsonl(str(path), previous=first.source, previous_offset=first.next_offset)

    assert second.bytes_scanned > 0
    assert second.latest_turn_id == "turn-2"
    assert second.next_offset == path.stat().st_size
