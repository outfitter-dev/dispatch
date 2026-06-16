"""Tests for the published-package smoke helper."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import check_pypi_smoke


def test_package_name_from_package_spec() -> None:
    assert check_pypi_smoke._package_name("outfitter-dispatch==0.8.0") == "outfitter-dispatch"
    assert check_pypi_smoke._package_name("outfitter-dispatch[dev]>=0.8") == "outfitter-dispatch"
    assert (
        check_pypi_smoke._package_name(
            "outfitter-dispatch @ https://example.test/outfitter-dispatch.whl"
        )
        == "outfitter-dispatch"
    )


def test_dispatch_refreshes_target_package_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="dispatch 0.8.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = check_pypi_smoke._dispatch(
        "outfitter-dispatch==0.8.0",
        ["--version"],
        {"DISPATCH_HOME": str(tmp_path)},
    )

    assert result.stdout == "dispatch 0.8.0\n"
    assert calls == [
        [
            "uvx",
            "--refresh-package",
            "outfitter-dispatch",
            "--from",
            "outfitter-dispatch==0.8.0",
            "dispatch",
            "--version",
        ]
    ]
