"""Config/preset resolution for ``dispatch new``."""

from __future__ import annotations

from pathlib import Path

import pytest

from outfitter.dispatch.config import config_path
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

[workspace]
default = "auto"
worktree = "create"
worktree_path = ".dispatch/wt-default"
worktree_branch = "dispatch/default"
worktree_base = "main"

[workspace.presets.athena]
mode = "auto"
worktree = "create"
worktree_path = ".dispatch/wt-athena"
worktree_branch = "dispatch/athena"
worktree_base = "origin/main"
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
    assert resolved.workspace.default == "auto"
    assert resolved.workspace.worktree == "create"
    assert resolved.workspace.worktree_path == str(repo / ".dispatch" / "wt-default")
    assert resolved.workspace.worktree_branch == "dispatch/default"
    assert resolved.workspace.worktree_base == "main"
    assert resolved.workspace.presets["athena"].mode == "auto"
    assert resolved.workspace.presets["athena"].worktree == "create"
    assert resolved.workspace.presets["athena"].worktree_path == str(
        repo / ".dispatch" / "wt-athena"
    )
    assert resolved.workspace.presets["athena"].worktree_branch == "dispatch/athena"
    assert resolved.workspace.presets["athena"].worktree_base == "origin/main"


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


def test_resolve_new_ignores_global_provider_runtime_table(tmp_path: Path) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        """
[defaults]
provider = "hermes"
prefix = "[global]"

[providers.hermes]
hermes_home = "/not-read-by-launch-resolution"
source_root = "/not-read-by-launch-resolution"
interpreter = "/not-read-by-launch-resolution"
"""
    )

    resolved = resolve_new(name="work", presets=[], cli=NewSettings(cwd=str(tmp_path)))

    assert resolved.settings.provider == "hermes"
    assert resolved.display_name == "[global] work"


def test_resolve_new_rejects_repo_provider_runtime_table(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text(
        """
[providers.hermes]
hermes_home = "/tmp"
source_root = "/tmp"
interpreter = "/bin/sh"
"""
    )

    with pytest.raises(ValidationError, match="invalid dispatch config"):
        resolve_new(name="work", presets=[], cli=NewSettings(cwd=str(tmp_path)))


def test_resolve_new_threads_provider_through_defaults_presets_and_cli(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text(
        """
[defaults]
provider = "codex"

[presets.claude-lane]
provider = "claude"
"""
    )

    defaults_only = resolve_new(name="work", presets=[], cli=NewSettings(cwd=str(tmp_path)))
    assert defaults_only.settings.provider == "codex"

    preset_wins = resolve_new(
        name="work", presets=["claude-lane"], cli=NewSettings(cwd=str(tmp_path))
    )
    assert preset_wins.settings.provider == "claude"

    cli_wins = resolve_new(
        name="work",
        presets=["claude-lane"],
        cli=NewSettings(cwd=str(tmp_path), provider="codex"),
    )
    assert cli_wins.settings.provider == "codex"


def test_resolve_new_drops_inherited_codex_overrides_for_hermes(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text(
        """
[defaults]
provider = "hermes"
model = "codex-model"
effort = "high"
sandbox = "read-only"
ephemeral = true
prefix = "[team]"
"""
    )

    resolved = resolve_new(name="work", presets=[], cli=NewSettings(cwd=str(tmp_path)))

    assert resolved.settings.provider == "hermes"
    assert resolved.settings.model is None
    assert resolved.settings.effort is None
    assert resolved.settings.sandbox is None
    assert resolved.settings.ephemeral is None
    assert resolved.settings.prefix == "[team]"


def test_resolve_new_rejects_selected_codex_preset_for_hermes(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text(
        """
[presets.fast]
effort = "low"
"""
    )

    with pytest.raises(ValidationError, match=r"preset 'fast'.*effort"):
        resolve_new(
            name="work",
            presets=["fast"],
            cli=NewSettings(cwd=str(tmp_path), provider="hermes"),
        )


def test_resolve_new_treats_explicit_false_as_a_hermes_override(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"operation input.*ephemeral"):
        resolve_new(
            name="work",
            presets=[],
            cli=NewSettings(cwd=str(tmp_path), provider="hermes", ephemeral=False),
        )


def test_resolve_new_rejects_codex_override_in_hermes_packet(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"launch packet.*model"):
        resolve_new(
            name="work",
            presets=[],
            cli=NewSettings(cwd=str(tmp_path), provider="hermes"),
            packet=NewSettings(model="codex-model"),
        )


def test_resolve_new_rejects_unknown_provider_in_config(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text('[defaults]\nprovider = "gemini"\n')

    with pytest.raises(ValidationError, match="invalid dispatch config"):
        resolve_new(name="work", presets=[], cli=NewSettings(cwd=str(tmp_path)))


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


def test_resolve_new_merges_global_and_repo_permission_profile_presets(
    tmp_path: Path,
) -> None:
    global_path = config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        '[defaults]\nmodel = "global-model"\n[presets.safe]\npermission_profile = ":read-only"\n'
    )
    repo = tmp_path / "repo"
    (repo / ".dispatch").mkdir(parents=True)
    (repo / ".dispatch" / "config.toml").write_text('[presets.safe]\neffort = "low"\n')

    resolved = resolve_new(name="work", presets=["safe"], cli=NewSettings(cwd=str(repo)))

    assert resolved.settings.model == "global-model"
    assert resolved.settings.permission_profile == ":read-only"
    assert resolved.settings.effort == "low"


def test_resolve_new_later_profile_clears_inherited_granular_policy(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text(
        '[defaults]\nsandbox = "read-only"\n[presets.profile]\npermission_profile = ":workspace"\n'
    )

    resolved = resolve_new(name="work", presets=["profile"], cli=NewSettings(cwd=str(tmp_path)))

    assert resolved.settings.permission_profile == ":workspace"
    assert resolved.settings.sandbox is None


def test_resolve_new_later_sandbox_clears_inherited_profile(tmp_path: Path) -> None:
    (tmp_path / ".dispatch").mkdir()
    (tmp_path / ".dispatch" / "config.toml").write_text(
        '[defaults]\npermission_profile = ":read-only"\n'
    )

    resolved = resolve_new(
        name="work",
        presets=[],
        cli=NewSettings(cwd=str(tmp_path), sandbox="workspace-write"),
    )

    assert resolved.settings.permission_profile is None
    assert resolved.settings.sandbox == "workspace-write"
