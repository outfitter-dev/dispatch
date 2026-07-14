from __future__ import annotations

import io
import json
import stat
import sys
from pathlib import Path

import pytest

from outfitter.dispatch.claude_statusline_cli import main
from outfitter.dispatch.core.claude_statusline import (
    ClaudeStatuslineSnapshot,
    StatuslineCaptureError,
    capture_claude_statusline,
    read_claude_statusline_snapshot,
)


def _payload(
    *, include_five_hour: bool = True, include_seven_day: bool = True
) -> dict[str, object]:
    rate_limits: dict[str, object] = {}
    if include_five_hour:
        rate_limits["five_hour"] = {"used_percentage": 23.5, "resets_at": 1_738_425_600}
    if include_seven_day:
        rate_limits["seven_day"] = {"used_percentage": 41.2, "resets_at": 1_738_857_600}
    return {
        "cwd": "/private/workspace",
        "session_id": "raw-session-id",
        "transcript_path": "/private/transcript.jsonl",
        "version": "2.1.206",
        "model": {"id": "claude-opus-private", "display_name": "Opus"},
        "rate_limits": rate_limits,
    }


def test_capture_statusline_atomically_persists_only_normalized_capacity(tmp_path: Path) -> None:
    path = tmp_path / "providers" / "claude" / "statusline.json"

    snapshot = capture_claude_statusline(
        json.dumps(_payload()).encode(),
        path=path,
        observed_at="2026-07-14T19:00:00+00:00",
    )

    assert snapshot == read_claude_statusline_snapshot(path=path)
    assert snapshot.rate_limits.five_hour is not None
    assert snapshot.rate_limits.five_hour.used_percentage == 23.5
    assert snapshot.rate_limits.five_hour.observed_at == "2026-07-14T19:00:00+00:00"
    assert snapshot.rate_limits.seven_day is not None
    assert snapshot.session_fingerprint is not None
    assert snapshot.session_fingerprint.startswith("sha256:")
    assert snapshot.model_label == "Opus"
    assert snapshot.claude_code_version == "2.1.206"
    persisted = path.read_text()
    for secret in (
        "raw-session-id",
        "/private/workspace",
        "/private/transcript.jsonl",
        "claude-opus-private",
    ):
        assert secret not in persisted
    assert not list(path.parent.glob("*.tmp"))


def test_capture_statusline_accepts_one_missing_window(tmp_path: Path) -> None:
    snapshot = capture_claude_statusline(
        json.dumps(_payload(include_seven_day=False)).encode(),
        path=tmp_path / "statusline.json",
        observed_at="2026-07-14T19:00:00+00:00",
    )

    assert snapshot.rate_limits.five_hour is not None
    assert snapshot.rate_limits.seven_day is None


def test_capture_statusline_merges_independent_windows_without_refreshing_retained_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "statusline.json"
    capture_claude_statusline(
        json.dumps(_payload(include_five_hour=False)).encode(),
        path=path,
        observed_at="2026-07-14T18:00:00+00:00",
    )

    snapshot = capture_claude_statusline(
        json.dumps(_payload(include_seven_day=False)).encode(),
        path=path,
        observed_at="2026-07-14T19:00:00+00:00",
    )

    assert snapshot.rate_limits.five_hour is not None
    assert snapshot.rate_limits.five_hour.observed_at == "2026-07-14T19:00:00+00:00"
    assert snapshot.rate_limits.seven_day is not None
    assert snapshot.rate_limits.seven_day.observed_at == "2026-07-14T18:00:00+00:00"


def test_capture_statusline_does_not_regress_to_older_capture(tmp_path: Path) -> None:
    path = tmp_path / "statusline.json"
    newer = capture_claude_statusline(
        json.dumps(_payload(include_five_hour=False)).encode(),
        path=path,
        observed_at="2026-07-14T20:00:00+00:00",
    )

    result = capture_claude_statusline(
        json.dumps(_payload(include_seven_day=False)).encode(),
        path=path,
        observed_at="2026-07-14T19:00:00+00:00",
    )

    assert result == newer
    assert read_claude_statusline_snapshot(path=path) == newer


def test_capture_statusline_fsyncs_containing_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory_synced = False
    real_fsync = __import__("os").fsync

    def fsync(fd: int) -> None:
        nonlocal directory_synced
        if stat.S_ISDIR(__import__("os").fstat(fd).st_mode):
            directory_synced = True
        real_fsync(fd)

    monkeypatch.setattr("outfitter.dispatch.core.claude_statusline.os.fsync", fsync)

    capture_claude_statusline(
        json.dumps(_payload()).encode(),
        path=tmp_path / "statusline.json",
        observed_at="2026-07-14T19:00:00+00:00",
    )

    assert directory_synced is True


def test_capture_statusline_records_absent_rate_limits_as_unavailable(tmp_path: Path) -> None:
    payload = _payload(include_five_hour=False, include_seven_day=False)
    payload.pop("rate_limits")

    snapshot = capture_claude_statusline(
        json.dumps(payload).encode(),
        path=tmp_path / "statusline.json",
        observed_at="2026-07-14T19:00:00+00:00",
    )

    assert snapshot.rate_limits.five_hour is None
    assert snapshot.rate_limits.seven_day is None


@pytest.mark.parametrize(
    "payload",
    [
        b"not json /private/detail",
        json.dumps({"rate_limits": {"five_hour": {"used_percentage": 101}}}).encode(),
        json.dumps({"rate_limits": {"five_hour": {"used_percentage": "private"}}}).encode(),
        b"x" * (1024 * 1024 + 1),
    ],
)
def test_capture_statusline_rejects_invalid_input_without_clobbering(
    tmp_path: Path, payload: bytes
) -> None:
    path = tmp_path / "statusline.json"
    original = ClaudeStatuslineSnapshot.model_validate(
        {
            "observed_at": "2026-07-14T18:00:00+00:00",
            "rate_limits": {},
        }
    )
    path.write_text(original.model_dump_json())

    with pytest.raises(StatuslineCaptureError):
        capture_claude_statusline(payload, path=path)

    assert read_claude_statusline_snapshot(path=path) == original
    assert "/private/detail" not in path.read_text()


def test_read_statusline_rejects_incompatible_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "statusline.json"
    path.write_text('{"schema_version": 999, "observed_at": "private"}')

    assert read_claude_statusline_snapshot(path=path) is None


def test_statusline_entrypoint_captures_without_rendering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = json.dumps(_payload()).encode()
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))

    main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert read_claude_statusline_snapshot() is not None


def test_statusline_entrypoint_reports_only_generic_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(io.BytesIO(b"private malformed statusline")),
    )

    with pytest.raises(SystemExit, match="1"):
        main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "dispatch: Claude statusline snapshot rejected\n"
    assert "private" not in captured.err
