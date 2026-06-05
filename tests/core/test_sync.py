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
                "payload": {"cwd": "/work", "model": "gpt-5-codex", "effort": "low"},
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
    assert facts.model == "gpt-5-codex"
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
