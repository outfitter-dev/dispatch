from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from outfitter.dispatch.surfaces.cli_outpost import build_outpost_cli


def test_pause_resume_affect_only_the_chosen_run_directory(tmp_path: Path) -> None:
    first, sibling = tmp_path / "first", tmp_path / "sibling"
    first.mkdir()
    sibling.mkdir()
    app = build_outpost_cli()
    runner = CliRunner()
    assert runner.invoke(app, ["pause", str(first)]).exit_code == 0
    assert (first / "paused").exists()
    assert not (sibling / "paused").exists()
    assert runner.invoke(app, ["resume", str(first)]).exit_code == 0
    assert not (first / "paused").exists()
