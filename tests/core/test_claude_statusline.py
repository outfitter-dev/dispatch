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
from outfitter.dispatch.core.usage_capture import (
    UsageCaptureRecord,
    write_usage_capture_record,
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
    assert snapshot.rate_limits_available is True
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
    assert snapshot.rate_limits_available is False


def test_capture_statusline_retains_last_windows_when_current_limits_are_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "statusline.json"
    previous = capture_claude_statusline(
        json.dumps(_payload()).encode(),
        path=path,
        observed_at="2026-07-14T18:00:00+00:00",
    )

    current = capture_claude_statusline(
        json.dumps({"session_id": "new-session", "version": "2.1.207"}).encode(),
        path=path,
        observed_at="2026-07-14T19:00:00+00:00",
    )

    assert current.observed_at == "2026-07-14T19:00:00+00:00"
    assert current.rate_limits_available is False
    assert current.rate_limits == previous.rate_limits
    assert current.rate_limits.five_hour is not None
    assert current.rate_limits.five_hour.observed_at == "2026-07-14T18:00:00+00:00"


@pytest.mark.parametrize(
    "payload",
    [
        b"not json /private/detail",
        json.dumps({"rate_limits": {"five_hour": {"used_percentage": 101}}}).encode(),
        json.dumps({"rate_limits": {"five_hour": {"used_percentage": "private"}}}).encode(),
        b"x" * (1024 * 1024 + 1),
        # Integer literal beyond CPython's int-conversion digit limit: json.loads
        # raises a bare ValueError, not JSONDecodeError.
        b'{"resets_at": ' + b"9" * 4400 + b"}",
        # In-limit integer too large for float(): used_percentage conversion
        # raises OverflowError, not ValidationError.
        b'{"rate_limits": {"five_hour": {"used_percentage": ' + b"9" * 400 + b"}}}",
        # Nesting deep enough to exhaust the recursion limit inside json.loads.
        b'{"a":' * 100_000 + b"1" + b"}" * 100_000,
        # Invalid UTF-8 bytes: json.loads raises UnicodeDecodeError.
        b'\xff\xfe{"a": 1}',
        # Escaped lone surrogate survives json.loads; _fingerprint's UTF-8
        # encode of session_id raises UnicodeEncodeError post-parse.
        b'{"session_id": "\\ud800"}',
        # Lone surrogate in any other captured string field is rejected by
        # pydantic string validation (ValidationError), never persisted.
        b'{"model": {"display_name": "x\\ud800y"}}',
    ],
)
def test_capture_statusline_rejects_invalid_input_without_clobbering(
    tmp_path: Path, payload: bytes
) -> None:
    path = tmp_path / "statusline.json"
    original = ClaudeStatuslineSnapshot.model_validate(
        {
            "observed_at": "2026-07-14T18:00:00+00:00",
            "rate_limits_available": False,
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
    monkeypatch.setattr(sys, "argv", ["dispatch-claude-statusline"])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert read_claude_statusline_snapshot() is not None


def test_statusline_entrypoint_fails_open_on_bad_payload_without_leaking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["dispatch-claude-statusline"])
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(io.BytesIO(b"private malformed statusline")),
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "dispatch: Claude statusline snapshot rejected\n"
    assert "private" not in captured.err


def test_statusline_entrypoint_never_delegates_even_with_recorded_renderer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The legacy helper is capture-only: a recorded original renderer is ignored.

    The documented wrapper pattern invokes this helper from the user's own
    statusline script; if the helper delegated to the recorded command (which
    may be that very wrapper), each refresh would recurse indefinitely.
    """
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    write_usage_capture_record(
        UsageCaptureRecord.model_validate(
            {
                "provider": "claude",
                "had_statusline": True,
                "original_statusline": {"type": "command", "command": "echo DELEGATED"},
                "installed_command": "dispatch usage-capture run --provider claude",
                "installed_at": "2026-08-29T12:00:00+00:00",
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["dispatch-claude-statusline"])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(_payload()).encode())))

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "DELEGATED" not in captured.out
    assert read_claude_statusline_snapshot() is not None


def test_statusline_entrypoint_help_notes_deprecation_and_capture_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["dispatch-claude-statusline", "--help"])

    main()

    captured = capsys.readouterr()
    assert "Deprecated capture-only helper" in captured.out
    assert "never" in captured.out and "delegates" in captured.out
    # Until `dispatch usage-capture install` ships, the wrapper pattern stays
    # the recommendation; hand-wiring `usage-capture run` is warned against
    # (without a managed restoration record it renders nothing).
    assert "wrapper" in captured.out
    assert "dispatch usage-capture install" in captured.out
    assert "Prefer `dispatch usage-capture run" not in captured.out
