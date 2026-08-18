"""Daemon host compatibility warnings."""

from __future__ import annotations

from pathlib import Path

import structlog
from pytest import MonkeyPatch
from structlog.testing import capture_logs

from outfitter.dispatch.daemon.host import _warn_if_codex_below_floor


async def test_daemon_warns_with_structured_below_floor_details(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\necho 'codex-cli 0.146.0'\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    with capture_logs() as logs:
        await _warn_if_codex_below_floor(structlog.get_logger())

    assert logs == [
        {
            "event": "dispatchd.codex_version_below_floor",
            "log_level": "warning",
            "minimum_version": "0.147.0",
            "path": str(binary),
            "version": "0.146.0",
        }
    ]


async def test_daemon_ignores_invalid_compatibility_manifest(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "codex"
    manifest = tmp_path / "protocol_manifest.json"
    binary.write_text("#!/bin/sh\necho 'codex-cli 0.147.0'\n")
    binary.chmod(0o755)
    manifest.write_text("{}")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr("outfitter.dispatch.codex_compat._manifest_path", lambda: manifest)

    with capture_logs() as logs:
        await _warn_if_codex_below_floor(structlog.get_logger())

    assert logs == []
