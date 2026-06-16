from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from outfitter.dispatch.config import config_path, runtime_policy


def test_runtime_policy_reads_local_config(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    config_path().write_text(
        "[policy]\nallow_attached_writes = true\nallow_workspace_setup = true\n"
        "workspace_setup_timeout_seconds = 30\n"
    )

    assert runtime_policy().allow_attached_writes is True
    assert runtime_policy().allow_workspace_setup is True
    assert runtime_policy().workspace_setup_timeout_seconds == 30


def test_runtime_policy_env_overrides_local_config(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    monkeypatch.setenv("DISPATCH_ALLOW_ATTACHED_WRITES", "0")
    config_path().write_text(
        "[policy]\nallow_attached_writes = true\nallow_workspace_setup = true\n"
        "workspace_setup_timeout_seconds = 45\n"
    )

    policy = runtime_policy()
    assert policy.allow_attached_writes is False
    assert policy.allow_workspace_setup is True
    assert policy.workspace_setup_timeout_seconds == 45
