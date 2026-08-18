"""Compatibility-manifest refresh policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.refresh_app_server_manifest import _minimum_codex_cli_version


def test_refresh_reads_hand_maintained_floor_from_existing_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"minimum_codex_cli_version": "0.147.0"}))

    assert _minimum_codex_cli_version(manifest) == "0.147.0"


def test_refresh_rejects_manifest_without_hand_maintained_floor(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")

    with pytest.raises(ValueError, match="minimum_codex_cli_version"):
        _minimum_codex_cli_version(manifest)
