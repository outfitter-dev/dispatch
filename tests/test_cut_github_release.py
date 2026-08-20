"""GitHub Release cutter: version in pyproject.toml is the publish trigger."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.cut_github_release import (
    plan_release,
    read_project_version,
    release_tag_for,
    write_github_output,
)


def test_read_project_version(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "outfitter-dispatch"\nversion = "0.11.0"\n')

    assert read_project_version(pyproject) == "0.11.0"


def test_release_tag_for_final_version() -> None:
    assert release_tag_for("0.11.0") == "v0.11.0"


def test_release_tag_for_rejects_prerelease() -> None:
    with pytest.raises(SystemExit, match="non-final"):
        release_tag_for("0.11.0rc1")


def test_plan_release_creates_missing_tag() -> None:
    plan = plan_release(version="0.11.0", existing_tags={"v0.10.0"})

    assert plan.action == "create"
    assert plan.tag == "v0.11.0"
    assert "v0.11.0" in plan.reason


def test_plan_release_skips_existing_tag() -> None:
    plan = plan_release(version="0.11.0", existing_tags={"refs/tags/v0.11.0"})

    assert plan.action == "skip"
    assert plan.reason == "v0.11.0 already exists"


def test_write_github_output_records_created_tag(tmp_path: Path) -> None:
    output = tmp_path / "github_output"
    plan = plan_release(version="0.11.0", existing_tags=set())

    write_github_output(plan, created=True, output_path=output)

    assert output.read_text() == "created=true\ntag=v0.11.0\n"
