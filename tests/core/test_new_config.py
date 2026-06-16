"""Config/preset resolution for ``dispatch new``."""

from __future__ import annotations

from pathlib import Path

import pytest

from outfitter.dispatch.contracts.errors import ValidationError
from outfitter.dispatch.core.new_config import NewSettings, resolve_new


def test_resolve_new_merges_defaults_presets_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "dispatch"
    (repo / ".dispatch" / "instructions").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".dispatch" / "instructions" / "builder.md").write_text("build safely")
    (repo / ".dispatch" / "config.toml").write_text(
        """
[defaults]
sandbox = "read-only"
approval_policy = "never"
model = "default-model"
prefix = "[${DISPATCH.CWD.REPO}]"

[presets.builder]
sandbox = "workspace-write"
approval_policy = "on-request"
developer_file = ".dispatch/instructions/builder.md"

[presets.fast]
effort = "low"
model = "fast-model"
"""
    )
    monkeypatch.chdir(tmp_path)

    resolved = resolve_new(
        name="work",
        presets=["builder", "fast"],
        cli=NewSettings(cwd=str(repo), model="cli-model"),
    )

    assert resolved.cwd == repo
    assert resolved.display_name == "[dispatch] work"
    assert resolved.handle == "@[dispatch] work"
    assert resolved.developer_instructions == "build safely"
    assert resolved.settings.sandbox == "workspace-write"
    assert resolved.settings.approval_policy == "on-request"
    assert resolved.settings.effort == "low"
    assert resolved.settings.model == "cli-model"


def test_resolve_new_ignores_runtime_policy_table(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text(
        """
[policy]
allow_attached_writes = true
"""
    )

    resolved = resolve_new(name="work", presets=[], cli=NewSettings(cwd=str(tmp_path)))

    assert resolved.settings.cwd == str(tmp_path)


def test_resolve_new_rejects_missing_preset(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text("[presets.ok]\neffort = 'low'\n")
    with pytest.raises(ValidationError, match="unknown preset"):
        resolve_new(name="x", presets=["missing"], cli=NewSettings(cwd=str(tmp_path)))


def test_resolve_new_rejects_unknown_prefix_variable(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unsupported prefix variable"):
        resolve_new(
            name="x",
            presets=[],
            cli=NewSettings(cwd=str(tmp_path), prefix="[${HOME}]"),
        )


def test_resolve_new_does_not_double_prefix(tmp_path: Path) -> None:
    resolved = resolve_new(
        name="[dispatch] review",
        presets=[],
        cli=NewSettings(cwd=str(tmp_path), prefix="[dispatch]"),
    )
    assert resolved.display_name == "[dispatch] review"
