"""Unit coverage for integration-harness availability checks."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from tests.integration import conftest as integration_fixtures


def test_integration_unavailable_reason_names_below_floor_binary(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\necho 'codex-cli 0.146.0'\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    reason = integration_fixtures._why_unavailable()

    assert reason == (f"codex-cli 0.146.0 at {binary} is below supported floor 0.147.0")
