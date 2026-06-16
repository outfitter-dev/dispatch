"""Workspace preflight discovery and setup policy."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from outfitter.dispatch.config import RuntimePolicy
from outfitter.dispatch.contracts.errors import ValidationError
from outfitter.dispatch.core.new_config import WorkspaceConfig
from outfitter.dispatch.core.workspace import plan_workspace, prepare_workspace


def test_plan_workspace_none_preserves_cwd(tmp_path: Path) -> None:
    resolved = plan_workspace(
        cwd=tmp_path,
        name="worker",
        requested="none",
        setup="auto",
        worktree="none",
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=WorkspaceConfig(),
        policy=RuntimePolicy(),
    )

    assert resolved.effective_cwd == tmp_path
    assert resolved.view.state == "disabled"
    assert resolved.view.setup.ran is False


def test_plan_workspace_auto_reports_missing_metadata(tmp_path: Path) -> None:
    resolved = plan_workspace(
        cwd=tmp_path,
        name="worker",
        requested="auto",
        setup="auto",
        worktree="none",
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=WorkspaceConfig(),
        policy=RuntimePolicy(),
    )

    assert resolved.effective_cwd == tmp_path
    assert resolved.view.state == "not_found"
    assert resolved.view.environment_file is None
    assert resolved.view.setup.policy == "not_found"


def test_plan_workspace_parses_codex_environment(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text(
        """
version = 1
name = "athena-vault"
extra = "recorded"

[setup]
script = "./.codex/hooks/workspace-bootstrap.sh"

[cleanup]
script = "./.codex/hooks/workspace-teardown.sh"
"""
    )

    resolved = plan_workspace(
        cwd=repo,
        name="worker",
        requested="auto",
        setup="auto",
        worktree="none",
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=WorkspaceConfig(),
        policy=RuntimePolicy(),
    )

    assert resolved.effective_cwd == repo
    assert resolved.view.state == "discovered"
    assert resolved.view.repo_root == str(repo)
    assert resolved.view.environment is not None
    assert resolved.view.environment.name == "athena-vault"
    assert resolved.view.environment.setup_script == "./.codex/hooks/workspace-bootstrap.sh"
    assert resolved.view.environment.cleanup_script == "./.codex/hooks/workspace-teardown.sh"
    assert resolved.view.environment.unknown_keys == ["extra"]
    assert resolved.view.setup.policy == "not_allowed"


def test_plan_workspace_uses_named_preset(tmp_path: Path) -> None:
    config = WorkspaceConfig.model_validate({"presets": {"athena": {"mode": "auto"}}})

    resolved = plan_workspace(
        cwd=tmp_path,
        name="worker",
        requested="athena",
        setup="auto",
        worktree="none",
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=config,
        policy=RuntimePolicy(),
    )

    assert resolved.view.mode == "athena"
    assert resolved.view.resolved_mode == "auto"


def test_plan_workspace_uses_configured_worktree_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "repo")
    root = tmp_path / "dispatch-worktrees"
    monkeypatch.setenv("DISPATCH_WORKTREE_ROOT", str(root))
    config = WorkspaceConfig.model_validate(
        {
            "default": "auto",
            "worktree": "create",
            "worktree_branch": "dispatch/from-config",
            "worktree_base": "HEAD",
        }
    )

    resolved = plan_workspace(
        cwd=repo,
        name="worker",
        requested=None,
        setup="auto",
        worktree=None,
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=config,
        policy=RuntimePolicy(),
    )

    assert resolved.view.mode == "auto"
    assert resolved.view.worktree.mode == "create"
    assert resolved.view.worktree.path == str(root / "repo" / "worker")
    assert resolved.view.worktree.branch == "dispatch/from-config"
    assert resolved.view.worktree.base == "HEAD"


def test_plan_workspace_preset_worktree_overrides_global_workspace_config(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    config = WorkspaceConfig.model_validate(
        {
            "worktree": "none",
            "worktree_branch": "dispatch/global",
            "presets": {
                "athena": {
                    "mode": "auto",
                    "worktree": "create",
                    "worktree_path": str(tmp_path / "athena-wt"),
                    "worktree_branch": "dispatch/athena",
                    "worktree_base": "HEAD",
                }
            },
        }
    )

    resolved = plan_workspace(
        cwd=repo,
        name="worker",
        requested="athena",
        setup="auto",
        worktree=None,
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=config,
        policy=RuntimePolicy(),
    )

    assert resolved.view.mode == "athena"
    assert resolved.view.worktree.mode == "create"
    assert resolved.view.worktree.path == str(tmp_path / "athena-wt")
    assert resolved.view.worktree.branch == "dispatch/athena"


def test_plan_workspace_cli_worktree_overrides_workspace_config(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    config = WorkspaceConfig.model_validate(
        {
            "default": "auto",
            "worktree": "create",
            "worktree_branch": "dispatch/from-config",
        }
    )

    resolved = plan_workspace(
        cwd=repo,
        name="worker",
        requested=None,
        setup="auto",
        worktree="none",
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=config,
        policy=RuntimePolicy(),
    )

    assert resolved.effective_cwd == repo
    assert resolved.view.worktree.mode == "none"
    assert resolved.view.worktree.state == "disabled"


def test_plan_workspace_rejects_invalid_environment_toml(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text("[setup\n")

    with pytest.raises(ValidationError, match="invalid workspace environment"):
        plan_workspace(
            cwd=repo,
            name="worker",
            requested="auto",
            setup="auto",
            worktree="none",
            worktree_path=None,
            worktree_branch=None,
            worktree_base=None,
            config=WorkspaceConfig(),
            policy=RuntimePolicy(),
        )


async def test_prepare_workspace_runs_setup_when_explicit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text(
        """
version = 1
name = "repo"

[setup]
script = "printf setup-ok"
"""
    )

    resolved = await prepare_workspace(
        cwd=repo,
        name="worker",
        requested="auto",
        setup="run",
        worktree="none",
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=WorkspaceConfig(),
        policy=RuntimePolicy(),
    )

    assert resolved.view.state == "setup_completed"
    assert resolved.view.setup.ran is True
    assert resolved.view.setup.policy == "explicit"
    assert resolved.view.setup.stdout_tail == "setup-ok"


async def test_prepare_workspace_rejects_failing_setup(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text(
        """
version = 1
name = "repo"

[setup]
script = "printf nope >&2; exit 7"
"""
    )

    with pytest.raises(ValidationError, match="workspace setup failed"):
        await prepare_workspace(
            cwd=repo,
            name="worker",
            requested="auto",
            setup="run",
            worktree="none",
            worktree_path=None,
            worktree_branch=None,
            worktree_base=None,
            config=WorkspaceConfig(),
            policy=RuntimePolicy(),
        )


def test_plan_workspace_reports_global_dispatch_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "repo")
    root = tmp_path / "dispatch-worktrees"
    monkeypatch.setenv("DISPATCH_WORKTREE_ROOT", str(root))

    resolved = plan_workspace(
        cwd=repo,
        name="[repo] Lane A",
        requested="none",
        setup="auto",
        worktree="create",
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=WorkspaceConfig(),
        policy=RuntimePolicy(),
    )

    assert resolved.effective_cwd == root / "repo" / "repo-lane-a"
    assert resolved.view.worktree.mode == "create"
    assert resolved.view.worktree.state == "planned"
    assert resolved.view.worktree.branch == "dispatch/repo-lane-a"
    assert resolved.view.worktree.created is False
    assert not resolved.effective_cwd.exists()


async def test_prepare_workspace_creates_git_worktree(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    target = tmp_path / "wt"

    resolved = await prepare_workspace(
        cwd=repo,
        name="lane",
        requested="none",
        setup="auto",
        worktree="create",
        worktree_path=str(target),
        worktree_branch="dispatch/lane",
        worktree_base=None,
        config=WorkspaceConfig(),
        policy=RuntimePolicy(),
    )

    assert resolved.effective_cwd == target
    assert resolved.view.worktree.state == "created"
    assert resolved.view.worktree.created is True
    assert (target / "README.md").read_text() == "hi\n"
    assert _run_git(target, "branch", "--show-current") == "dispatch/lane"


async def test_prepare_workspace_rejects_branch_checked_out_elsewhere(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    first = tmp_path / "first"
    _run_git(repo, "worktree", "add", "-b", "dispatch/lane", str(first), "HEAD")

    with pytest.raises(ValidationError, match="already checked out"):
        await prepare_workspace(
            cwd=repo,
            name="lane",
            requested="none",
            setup="auto",
            worktree="create",
            worktree_path=str(tmp_path / "second"),
            worktree_branch="dispatch/lane",
            worktree_base=None,
            config=WorkspaceConfig(),
            policy=RuntimePolicy(),
        )


def _git_repo(path: Path) -> Path:
    path.mkdir()
    _run_git(path, "init", "-q")
    _run_git(path, "config", "user.email", "dispatch@example.test")
    _run_git(path, "config", "user.name", "Dispatch Test")
    (path / "README.md").write_text("hi\n")
    _run_git(path, "add", "README.md")
    _run_git(path, "commit", "-qm", "init")
    return path


def _run_git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout.strip()
