"""Codex CLI compatibility contract and version ordering."""

from __future__ import annotations

from pathlib import Path

import pytest

from outfitter.dispatch.codex_compat import (
    CodexVersion,
    inspect_codex_binary,
    minimum_codex_cli_version,
)


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("0.146.0", "0.147.0"),
        ("0.148.0-alpha.9", "0.148.0-alpha.11"),
        ("0.148.0-alpha.9", "0.148.0-alpha.9.2"),
        ("0.148.0-alpha.9.2", "0.148.0-alpha.11"),
        ("0.148.0-alpha.11", "0.148.0"),
    ],
)
def test_codex_version_orders_release_and_prerelease_shapes(older: str, newer: str) -> None:
    assert CodexVersion.parse(older) < CodexVersion.parse(newer)


@pytest.mark.parametrize(
    ("reported", "supported"),
    [
        ("0.146.0", False),
        ("0.147.0-alpha.11", False),
        ("0.147.0", True),
        ("0.148.0-alpha.1", True),
        ("0.148.0", True),
    ],
)
def test_inspect_codex_binary_applies_manifest_floor(
    tmp_path: Path, reported: str, supported: bool
) -> None:
    binary = tmp_path / "codex"
    binary.write_text(f"#!/bin/sh\necho 'codex-cli {reported}'\n")
    binary.chmod(0o755)

    result = inspect_codex_binary(str(binary))

    assert result.path == str(binary)
    assert result.version == reported
    assert result.minimum_version == minimum_codex_cli_version()
    assert result.supported is supported
