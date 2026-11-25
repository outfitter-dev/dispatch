from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from outfitter.dispatch.core.outpost_runner import prepare_run
from outfitter.dispatch.surfaces.cli_outpost import build_outpost_cli


def test_pause_resume_affect_only_the_chosen_run_directory(tmp_path: Path) -> None:
    first, sibling = tmp_path / "first", tmp_path / "sibling"
    prepare_run(first)
    prepare_run(sibling)
    app = build_outpost_cli()
    runner = CliRunner()
    assert runner.invoke(app, ["pause", str(first)]).exit_code == 0
    assert (first / "paused").exists()
    assert not (sibling / "paused").exists()
    assert runner.invoke(app, ["resume", str(first)]).exit_code == 0
    assert not (first / "paused").exists()


def test_resume_preserves_an_unrelated_directorys_pause_file(tmp_path: Path) -> None:
    (tmp_path / "paused").write_text("unrelated work")
    result = CliRunner().invoke(build_outpost_cli(), ["resume", str(tmp_path)])
    assert result.exit_code != 0
    assert (tmp_path / "paused").read_text() == "unrelated work"
