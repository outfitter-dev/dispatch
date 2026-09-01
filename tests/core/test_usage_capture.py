from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from outfitter.dispatch.contracts.errors import ValidationError
from outfitter.dispatch.core.usage_capture import (
    RECORD_FILENAME,
    WRAPPER_FILENAME,
    UsageCaptureRecord,
    ensure_private_dir,
    read_usage_capture_record,
    record_too_large,
    usage_capture_record_path,
    usage_capture_wrapper_path,
    write_private_file,
    write_usage_capture_record,
)


def _record(**overrides: object) -> UsageCaptureRecord:
    payload: dict[str, object] = {
        "provider": "claude",
        "had_statusline": True,
        "original_statusline": {
            "type": "command",
            "command": "~/.claude/statusline.sh",
            "padding": 0,
            "refreshInterval": 300,
            "hideVimModeIndicator": True,
            "futureUnknownKey": {"nested": ["kept", 1, None]},
        },
        "installed_command": "dispatch usage-capture run --provider claude",
        "installed_at": "2026-08-29T12:00:00+00:00",
    }
    payload.update(overrides)
    return UsageCaptureRecord.model_validate(payload)


def test_default_paths_live_under_dispatch_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "home"))

    assert usage_capture_wrapper_path() == tmp_path / "home" / "claude" / WRAPPER_FILENAME
    assert usage_capture_record_path() == tmp_path / "home" / "claude" / RECORD_FILENAME


def test_record_round_trip_preserves_unknown_statusline_keys_verbatim(tmp_path: Path) -> None:
    path = tmp_path / "claude" / RECORD_FILENAME
    original = _record()

    write_usage_capture_record(original, path=path)
    loaded = read_usage_capture_record(path=path)

    assert loaded == original
    assert loaded is not None
    assert loaded.original_statusline is not None
    assert loaded.original_statusline["futureUnknownKey"] == {"nested": ["kept", 1, None]}
    persisted = json.loads(path.read_text())
    assert persisted["original_statusline"]["hideVimModeIndicator"] is True


def test_record_write_is_owner_only_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "claude" / RECORD_FILENAME

    write_usage_capture_record(_record(), path=path)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp"))


def test_write_private_file_supports_executable_mode(tmp_path: Path) -> None:
    directory = ensure_private_dir(tmp_path / "claude")
    wrapper = directory / WRAPPER_FILENAME

    write_private_file(wrapper, b"#!/bin/sh\n", mode=0o700)

    assert stat.S_IMODE(wrapper.stat().st_mode) == 0o700
    assert wrapper.read_bytes() == b"#!/bin/sh\n"


def test_original_command_extraction() -> None:
    assert _record().original_command() == "~/.claude/statusline.sh"
    assert _record(had_statusline=False, original_statusline=None).original_command() is None
    assert _record(original_statusline={"type": "command"}).original_command() is None
    assert _record(original_statusline={"command": "   "}).original_command() is None
    assert _record(original_statusline={"command": 7}).original_command() is None


def test_record_rejects_naive_installed_at() -> None:
    with pytest.raises(PydanticValidationError):
        _record(installed_at="2026-08-29T12:00:00")


def _record_padded_to(total_bytes: int) -> UsageCaptureRecord:
    """A record whose write serialization (JSON + trailing newline) is exactly
    ``total_bytes`` long, padded via the original command string."""
    base = _record(original_statusline={"type": "command", "command": ""})
    overhead = len(base.model_dump_json(indent=2).encode()) + 1
    return _record(
        original_statusline={"type": "command", "command": "x" * (total_bytes - overhead)}
    )


def test_write_rejects_record_the_reader_would_refuse(tmp_path: Path) -> None:
    path = tmp_path / "claude" / RECORD_FILENAME
    oversize = _record_padded_to(64 * 1024 + 1)
    assert record_too_large(oversize)

    with pytest.raises(ValidationError):
        write_usage_capture_record(oversize, path=path)

    assert not path.exists()
    assert not path.parent.exists()  # guard fires before any filesystem work


def test_write_accepts_record_at_the_read_limit(tmp_path: Path) -> None:
    path = tmp_path / "claude" / RECORD_FILENAME
    at_limit = _record_padded_to(64 * 1024)
    assert not record_too_large(at_limit)

    write_usage_capture_record(at_limit, path=path)

    assert path.stat().st_size == 64 * 1024
    assert read_usage_capture_record(path=path) == at_limit
    assert not list(path.parent.glob("*.tmp"))


def test_read_record_fails_open_on_missing_corrupt_or_oversize(tmp_path: Path) -> None:
    missing = tmp_path / "claude" / RECORD_FILENAME
    assert read_usage_capture_record(path=missing) is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_bytes(b"{not json")
    assert read_usage_capture_record(path=corrupt) is None

    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text('{"schema_version": 999}')
    assert read_usage_capture_record(path=wrong_shape) is None

    oversize = tmp_path / "oversize.json"
    oversize.write_bytes(b"x" * (64 * 1024 + 1))
    assert read_usage_capture_record(path=oversize) is None
