"""Internal Claude background launch and identity reconciliation tests."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from outfitter.dispatch.core import claude_process
from outfitter.dispatch.core.claude_launch import (
    ClaudeLaunchAmbiguousError,
    ClaudeLaunchArgvLimitError,
    ClaudeLaunchEnvelope,
    ClaudeLaunchOutputError,
    ClaudeLaunchOutputLimitError,
    ClaudeLaunchTimeoutError,
    ClaudeLaunchValidationError,
    ClaudeProcessResult,
    launch_claude_background,
    preflight_claude_launch,
    project_claude_launch_argv,
    reconcile_claude_launch,
    run_claude_process,
)


def test_launch_argv_omits_defaults_and_keeps_prompt_as_one_argument(tmp_path: Path) -> None:
    prompt = "quotes: ' \"\nline two; $(touch nope) | café 🐍"
    envelope = ClaudeLaunchEnvelope(cwd=tmp_path, initial_text=prompt)

    argv = project_claude_launch_argv(envelope)

    assert argv == ("claude", "--bg", "--", prompt)
    assert argv[-1] is prompt
    assert "prompt" not in repr(envelope).lower()
    assert prompt not in repr(envelope)


def test_launch_argv_projects_supported_explicit_options(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    additional = tmp_path / "additional"
    plugin.mkdir()
    additional.mkdir()
    envelope = ClaudeLaunchEnvelope(
        cwd=tmp_path,
        initial_text="start",
        display_name="worker",
        agent="reviewer",
        model="sonnet",
        effort="high",
        permission_mode="default",
        settings="/tmp/settings.json",
        setting_sources=("user", "project"),
        mcp_config="/tmp/mcp.json",
        strict_mcp_config=True,
        plugin_dirs=(plugin,),
        additional_dirs=(additional,),
        worktree="feature",
    )

    assert project_claude_launch_argv(envelope) == (
        "claude",
        "--bg",
        "--name",
        "worker",
        "--agent",
        "reviewer",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--permission-mode",
        "default",
        "--settings",
        "/tmp/settings.json",
        "--setting-sources",
        "user,project",
        "--mcp-config",
        "/tmp/mcp.json",
        "--strict-mcp-config",
        "--plugin-dir",
        str(plugin),
        "--add-dir",
        str(additional),
        "--worktree",
        "feature",
        "--",
        "start",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"effort": "extreme"},
        {"permission_mode": "allowEverything"},
        {"strict_mcp_config": True},
        {"setting_sources": ()},
        {"setting_sources": ("user", "user")},
        {"plugin_dirs": (Path("relative"),)},
        {"worktree": ""},
        {"provider": "codex"},
    ],
)
def test_launch_validation_rejects_unsupported_options_before_invocation(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    envelope = ClaudeLaunchEnvelope(cwd=tmp_path, initial_text="safe")

    with pytest.raises(ClaudeLaunchValidationError):
        project_claude_launch_argv(ClaudeLaunchEnvelope(**{**envelope.__dict__, **changes}))


def test_reconcile_unique_full_uuid(tmp_path: Path) -> None:
    session_id = "518b912b-1234-4abc-8def-1234567890ab"
    roster = json.dumps(
        [
            {
                "id": "518b912b",
                "sessionId": session_id,
                "cwd": "/effective/worktree",
                "name": "worker",
                "kind": "background",
                "state": "active",
                "worktree": "/effective/worktree",
            }
        ]
    )

    observation = reconcile_claude_launch(
        short_id="518b912b", launch_cwd=tmp_path, roster_output=roster
    )

    assert observation.reconciliation == "reconciled"
    assert observation.provider_session_id == session_id
    assert observation.observed_cwd == "/effective/worktree"
    assert observation.observed_worktree == "/effective/worktree"


def test_reconcile_absent_or_not_yet_identified_is_pending(tmp_path: Path) -> None:
    absent = reconcile_claude_launch(short_id="518b912b", launch_cwd=tmp_path, roster_output="[]")
    provisional = reconcile_claude_launch(
        short_id="518b912b",
        launch_cwd=tmp_path,
        roster_output=json.dumps([{"id": "518b912b", "state": "starting"}]),
    )

    assert absent.reconciliation == "pending"
    assert absent.provider_session_id is None
    assert provisional.reconciliation == "pending"
    assert provisional.observed_state == "starting"


def test_reconcile_duplicate_or_incompatible_roster_fails_closed(tmp_path: Path) -> None:
    duplicate = json.dumps([{"id": "518b912b"}, {"id": "518b912b"}])
    with pytest.raises(ClaudeLaunchAmbiguousError):
        reconcile_claude_launch(short_id="518b912b", launch_cwd=tmp_path, roster_output=duplicate)
    with pytest.raises(ClaudeLaunchOutputError):
        reconcile_claude_launch(
            short_id="518b912b",
            launch_cwd=tmp_path,
            roster_output=json.dumps([{"id": "518b912b", "sessionId": "short"}]),
        )
    with pytest.raises(ClaudeLaunchOutputError):
        reconcile_claude_launch(
            short_id="518b912b",
            launch_cwd=tmp_path,
            roster_output=json.dumps([{"id": "518B912B", "kind": "interactive"}]),
        )
    with pytest.raises(ClaudeLaunchOutputError):
        reconcile_claude_launch(
            short_id="518b912b", launch_cwd=tmp_path, roster_output="private malformed"
        )


async def test_launch_invokes_exact_argv_then_global_unscoped_roster(tmp_path: Path) -> None:
    prompt = "one positional\n'quoted' $(unsafe) λ"
    session_id = "518b912b-1234-4abc-8def-1234567890ab"
    calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    async def run(
        argv: tuple[str, ...], cwd: Path, environment: Mapping[str, str]
    ) -> ClaudeProcessResult:
        calls.append((argv, cwd, dict(environment)))
        if argv == ("claude", "agents", "--json", "--all"):
            return ClaudeProcessResult(0, json.dumps([{"id": "518b912b", "sessionId": session_id}]))
        return ClaudeProcessResult(0, "518b912b\n")

    result = await launch_claude_background(
        ClaudeLaunchEnvelope(cwd=tmp_path, initial_text=prompt),
        run_process=run,
        environment={"PATH": "/usr/bin"},
        arg_max=100_000,
    )

    assert [call[0] for call in calls] == [
        ("claude", "--bg", "--", prompt),
        ("claude", "agents", "--json", "--all"),
    ]
    assert all(call[1] == tmp_path for call in calls)
    assert result.provider_session_id == session_id


async def test_validation_happens_before_process_invocation(tmp_path: Path) -> None:
    called = False

    async def run(
        _argv: tuple[str, ...], _cwd: Path, _environment: Mapping[str, str]
    ) -> ClaudeProcessResult:
        nonlocal called
        called = True
        return ClaudeProcessResult(0, "518b912b")

    with pytest.raises(ClaudeLaunchValidationError):
        await launch_claude_background(
            ClaudeLaunchEnvelope(
                cwd=tmp_path,
                initial_text="private prompt",
                strict_mcp_config=True,
            ),
            run_process=run,
        )
    assert called is False


def test_platform_preflight_accounts_for_argv_and_environment_without_leaking() -> None:
    prompt = "private prompt body"
    with pytest.raises(ClaudeLaunchArgvLimitError) as caught:
        preflight_claude_launch(
            ("claude", "--bg", "--", prompt), {"PRIVATE_ENV": "sensitive"}, arg_max=32
        )

    assert prompt not in str(caught.value)
    assert "sensitive" not in str(caught.value)


def test_platform_preflight_rejects_invalid_environment() -> None:
    with pytest.raises(ClaudeLaunchValidationError):
        preflight_claude_launch(("claude", "--bg", "safe"), {"INVALID=KEY": "value"})


async def test_process_runner_bounds_output(tmp_path: Path) -> None:
    executable = tmp_path / "oversized"
    executable.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(b'x' * (1024 * 1024 + 1))\n"
    )
    executable.chmod(0o755)

    with pytest.raises(ClaudeLaunchOutputLimitError):
        await run_claude_process((str(executable),), tmp_path, os.environ)


async def test_process_runner_times_out_and_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "slow"
    executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    executable.chmod(0o755)
    monkeypatch.setattr(claude_process, "_COMMAND_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(ClaudeLaunchTimeoutError):
        await run_claude_process((str(executable),), tmp_path, os.environ)


async def test_errors_and_observations_do_not_retain_prompt(tmp_path: Path) -> None:
    prompt = "never retain this raw prompt"

    async def run(
        argv: tuple[str, ...], _cwd: Path, _environment: Mapping[str, str]
    ) -> ClaudeProcessResult:
        if argv == ("claude", "agents", "--json", "--all"):
            return ClaudeProcessResult(0, "[]")
        return ClaudeProcessResult(0, "518b912b")

    observation = await launch_claude_background(
        ClaudeLaunchEnvelope(cwd=tmp_path, initial_text=prompt),
        run_process=run,
        environment={},
        arg_max=100_000,
    )

    assert prompt not in repr(observation)
    assert prompt not in json.dumps(observation.__dict__)
