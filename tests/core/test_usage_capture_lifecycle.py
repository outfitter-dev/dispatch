"""Install/status/remove lifecycle: temp Claude and Dispatch homes only."""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from outfitter.dispatch.core import usage_capture
from outfitter.dispatch.core import usage_capture_lifecycle as lifecycle
from outfitter.dispatch.core.claude_statusline import ClaudeStatuslineSnapshot
from outfitter.dispatch.core.usage_capture import (
    read_usage_capture_record,
    usage_capture_record_path,
    usage_capture_wrapper_path,
    write_private_file,
)
from outfitter.dispatch.core.usage_capture_lifecycle import (
    RUN_COMMAND,
    ArtifactsChangedError,
    SettingsChangedError,
    apply_install,
    apply_remove,
    environment_warnings,
    installed_command,
    plan_install,
    plan_remove,
    usage_capture_status,
    wrapper_content,
)
from outfitter.dispatch.core.usage_capture_settings import (
    ClaudeStatuslineEnvironment,
    inspect_claude_environment,
)

# The real resolver, captured before the autouse pin replaces the module
# attribute, so it can be unit-tested directly.
_REAL_RESOLVE = lifecycle._resolve_dispatch_executable


@pytest.fixture(autouse=True)
def pinned_dispatch_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin executable resolution to a real file so wrapper bytes are deterministic.

    Install bakes the dispatch executable resolved at install time into the
    wrapper, and status verifies that a baked absolute executable still exists
    and is executable — so the pinned path must actually exist, and pinning
    keeps the bytes independent of whether the test environment has
    ``dispatch`` on PATH (uv run does; a bare CI runner may not).
    """
    executable = tmp_path / "dispatch-env" / "bin" / "dispatch"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    monkeypatch.setattr(lifecycle, "_resolve_dispatch_executable", lambda: executable)
    return executable


def _run_command_for(executable: Path) -> str:
    return f"{shlex.quote(str(executable))} usage-capture run --provider claude"


ORIGINAL_STATUSLINE: dict[str, object] = {
    "type": "command",
    "command": "~/bin/original-statusline.sh",
    "padding": 0,
    "refreshInterval": 300,
    "hideVimModeIndicator": True,
    "futureUnknownKey": {"nested": ["kept", 1, None]},
}
BASE_SETTINGS: dict[str, object] = {
    "model": "opus",
    "hooks": {"PreToolUse": []},
    "statusLine": ORIGINAL_STATUSLINE,
}


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    return tmp_path / "claude-home" / "settings.json"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "dispatch-home" / "claude"


def _write_settings(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _env(settings_path: Path, project_dir: Path) -> ClaudeStatuslineEnvironment:
    return inspect_claude_environment(settings_path=settings_path, project_dir=project_dir)


def _install(
    settings_path: Path,
    project_dir: Path,
    directory: Path,
    *,
    yes_settings: dict[str, object] | None = None,
) -> None:
    if yes_settings is not None:
        _write_settings(settings_path, yes_settings)
    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)
    apply_install(env, plan)


def _state(settings_path: Path, project_dir: Path, directory: Path) -> str:
    return usage_capture_status(
        _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
    ).state


def test_install_with_existing_statusline_replaces_only_command(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)

    settings = json.loads(settings_path.read_text())
    wrapper = usage_capture_wrapper_path(directory)
    assert settings["model"] == "opus"
    assert settings["hooks"] == {"PreToolUse": []}
    assert settings["statusLine"]["command"] == str(wrapper)
    for key, value in ORIGINAL_STATUSLINE.items():
        if key != "command":
            assert settings["statusLine"][key] == value
    assert wrapper.read_text() == wrapper_content()
    assert stat.S_IMODE(wrapper.stat().st_mode) == 0o700
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.had_statusline is True
    assert record.original_statusline == ORIGINAL_STATUSLINE


def test_install_without_statusline_records_absence_and_installs(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings={"model": "opus"})

    settings = json.loads(settings_path.read_text())
    assert settings["statusLine"] == {
        "type": "command",
        "command": str(usage_capture_wrapper_path(directory)),
    }
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.had_statusline is False
    assert record.original_statusline is None


def test_reinstall_is_idempotent_and_never_records_wrapper_as_original(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    first_record = usage_capture_record_path(directory).read_bytes()

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.state == "installed"
    assert not plan.changes_anything
    apply_install(env, plan)
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.original_command() == "~/bin/original-statusline.sh"
    assert usage_capture_record_path(directory).read_bytes() == first_record


def test_reinstall_refreshes_drifted_wrapper_without_touching_record(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    wrapper = usage_capture_wrapper_path(directory)
    wrapper.write_text("#!/bin/sh\necho drifted\n")
    settings_before = settings_path.read_bytes()

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)
    assert plan.write_wrapper is True
    assert plan.write_record is False
    assert plan.write_settings is False
    apply_install(env, plan)

    assert wrapper.read_text() == wrapper_content()
    assert settings_path.read_bytes() == settings_before


def test_install_refuses_drifted_settings_and_preserves_record(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = {"type": "command", "command": "/usr/local/bin/newer.sh"}
    _write_settings(settings_path, settings)
    record_before = usage_capture_record_path(directory).read_bytes()
    settings_before = settings_path.read_bytes()

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.state == "drifted"
    assert plan.blocked is not None
    assert not plan.changes_anything
    with pytest.raises(RuntimeError):
        apply_install(env, plan)
    assert usage_capture_record_path(directory).read_bytes() == record_before
    assert settings_path.read_bytes() == settings_before


def test_install_drift_block_names_keep_current_recovery(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = {"type": "command", "command": "/usr/local/bin/newer.sh"}
    _write_settings(settings_path, settings)

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is not None
    assert "usage-capture remove" in plan.blocked
    assert "--keep-current" in plan.blocked


def test_install_after_keep_current_removal_rebaselines_to_current(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    newer = {"type": "command", "command": "/usr/local/bin/newer.sh"}
    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = newer
    _write_settings(settings_path, settings)

    env = _env(settings_path, project_dir)
    apply_remove(env, plan_remove(env, directory=directory, keep_current=True))
    _install(settings_path, project_dir, directory)

    assert _state(settings_path, project_dir, directory) == "installed"
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.had_statusline is True
    assert record.original_statusline == newer


def test_install_blocked_on_malformed_settings(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not json")

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.blocked is not None
    assert "not valid JSON" in plan.blocked
    with pytest.raises(RuntimeError):
        apply_install(env, plan)
    assert not usage_capture_wrapper_path(directory).exists()


# --- oversized statusLine: the record must stay readable or install must block ---


@pytest.mark.parametrize(
    "statusline",
    [
        {"type": "command", "command": "x" * (64 * 1024)},
        {"type": "command", "command": "~/bin/render.sh", "futureBulkyKey": ["y" * 1024] * 64},
    ],
)
def test_install_blocked_when_statusline_too_large_to_preserve(
    settings_path: Path, project_dir: Path, directory: Path, statusline: dict[str, object]
) -> None:
    # read_usage_capture_record rejects records above its size cap, so writing
    # one would strand the install: the wrapper runs capture-only (renderer
    # lost), status reads broken, and remove has no restore path. Install must
    # block with settings and artifacts untouched.
    _write_settings(settings_path, {"model": "opus", "statusLine": statusline})
    before = settings_path.read_bytes()

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.blocked is not None
    assert "too large" in plan.blocked
    assert not plan.changes_anything
    with pytest.raises(RuntimeError):
        apply_install(env, plan)
    assert settings_path.read_bytes() == before
    assert not usage_capture_wrapper_path(directory).exists()
    assert not usage_capture_record_path(directory).exists()


def test_install_statusline_just_under_record_cap_installs_and_restores(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # Boundary: a statusLine whose serialized record lands just under the
    # read-side cap must install normally and stay fully restorable.
    _write_settings(settings_path, {"statusLine": {"type": "command", "command": ""}})
    env = _env(settings_path, project_dir)
    base = lifecycle._fresh_record(env, usage_capture_wrapper_path(directory), None)
    base_size = len(base.model_dump_json(indent=2).encode()) + 1
    # Small margin absorbs timestamp-length variance between this probe record
    # and the one apply_install serializes.
    pad = usage_capture._MAX_RECORD_BYTES - base_size - 16
    original = {"type": "command", "command": "x" * pad}
    _install(settings_path, project_dir, directory, yes_settings={"statusLine": original})

    assert _state(settings_path, project_dir, directory) == "installed"
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.original_statusline == original

    env = _env(settings_path, project_dir)
    apply_remove(env, plan_remove(env, directory=directory))
    assert json.loads(settings_path.read_text())["statusLine"] == original


def test_environment_warnings_report_actionable_constraints(
    settings_path: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings(settings_path, {"disableAllHooks": True})
    local = project_dir / ".claude" / "settings.local.json"
    _write_settings(local, {"statusLine": {"type": "command", "command": "other"}})
    # The PATH warning applies only to the bare-`dispatch` fallback wrapper,
    # so the resolver must fail for it to fire.
    monkeypatch.setattr(lifecycle, "_resolve_dispatch_executable", lambda: None)

    env = _env(settings_path, project_dir)
    warnings = environment_warnings(env, dispatch_on_path=False)

    assert env.disable_all_hooks is True
    assert env.override_paths == (local,)
    assert any("disableAllHooks" in warning for warning in warnings)
    assert any(str(local) in warning for warning in warnings)
    assert any("not resolvable on PATH" in warning for warning in warnings)


def test_no_path_warning_when_absolute_executable_is_baked(
    settings_path: Path, project_dir: Path
) -> None:
    # The autouse fixture resolves a real absolute executable, which install
    # bakes into the wrapper: the wrapper then runs regardless of PATH, so
    # warning that it "will fail" would be false.
    _write_settings(settings_path, {"model": "opus"})

    warnings = environment_warnings(_env(settings_path, project_dir), dispatch_on_path=False)

    assert all("not resolvable on PATH" not in warning for warning in warnings)


def test_state_not_installed(settings_path: Path, project_dir: Path, directory: Path) -> None:
    _write_settings(settings_path, {"model": "opus"})
    assert _state(settings_path, project_dir, directory) == "not_installed"


def test_state_prepared_when_settings_still_hold_the_original(
    settings_path: Path, project_dir: Path, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings(settings_path, BASE_SETTINGS)
    # Simulate a crash after record+wrapper but before the settings write.
    monkeypatch.setattr(lifecycle, "write_claude_settings", _raise_os_error, raising=True)
    env = _env(settings_path, project_dir)
    with pytest.raises(OSError):
        apply_install(env, plan_install(env, directory=directory, dispatch_on_path=True))
    monkeypatch.undo()

    assert _state(settings_path, project_dir, directory) == "prepared"


def test_state_installed_then_drifted_then_broken(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    assert _state(settings_path, project_dir, directory) == "installed"

    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = {"type": "command", "command": "/usr/local/bin/newer.sh"}
    _write_settings(settings_path, settings)
    assert _state(settings_path, project_dir, directory) == "drifted"

    settings["statusLine"] = {
        "type": "command",
        "command": str(usage_capture_wrapper_path(directory)),
    }
    _write_settings(settings_path, settings)
    usage_capture_wrapper_path(directory).unlink()
    assert _state(settings_path, project_dir, directory) == "broken"


def test_state_broken_when_record_invalid_while_pointing_at_wrapper(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    usage_capture_record_path(directory).write_text("{not json")

    assert _state(settings_path, project_dir, directory) == "broken"


def test_state_disabled_when_installed_but_suppressed(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["disableAllHooks"] = True
    _write_settings(settings_path, settings)

    assert _state(settings_path, project_dir, directory) == "disabled"


def test_status_reports_facts_without_the_original_command(
    settings_path: Path, project_dir: Path, directory: Path, tmp_path: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot = ClaudeStatuslineSnapshot(
        observed_at="2026-08-29T12:00:00+00:00", rate_limits_available=False
    )
    snapshot_path.write_text(snapshot.model_dump_json())

    status = usage_capture_status(
        _env(settings_path, project_dir),
        directory=directory,
        snapshot_path=snapshot_path,
        now=datetime(2026, 8, 29, 12, 5, tzinfo=UTC),
        dispatch_on_path=True,
    )

    assert status.state == "installed"
    assert status.original_renderer_recorded is True
    assert status.settings_points_at_wrapper is True
    assert status.wrapper_current is True
    assert status.record_valid is True
    assert status.last_capture_at == "2026-08-29T12:00:00+00:00"
    assert status.capture_fresh is True
    assert "original-statusline.sh" not in status.model_dump_json()

    stale = usage_capture_status(
        _env(settings_path, project_dir),
        directory=directory,
        snapshot_path=snapshot_path,
        now=datetime(2026, 8, 29, 13, 0, tzinfo=UTC),
        dispatch_on_path=True,
    )
    assert stale.capture_fresh is False


def test_remove_restores_the_exact_original_statusline(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)

    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory)
    assert plan.blocked is None
    assert plan.restore_settings is True
    apply_remove(env, plan)

    settings = json.loads(settings_path.read_text())
    assert settings["statusLine"] == ORIGINAL_STATUSLINE
    assert settings["model"] == "opus"
    assert not usage_capture_wrapper_path(directory).exists()
    assert not usage_capture_record_path(directory).exists()
    assert not directory.exists()  # emptied directory is removed


def test_remove_deletes_the_key_when_no_statusline_existed(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings={"model": "opus"})

    env = _env(settings_path, project_dir)
    apply_remove(env, plan_remove(env, directory=directory))

    settings = json.loads(settings_path.read_text())
    assert "statusLine" not in settings
    assert settings["model"] == "opus"


def test_remove_refuses_drifted_settings_by_default(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = {"type": "command", "command": "/usr/local/bin/newer.sh"}
    _write_settings(settings_path, settings)

    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory)

    assert plan.blocked is not None
    assert "--keep-current" in plan.blocked
    with pytest.raises(RuntimeError):
        apply_remove(env, plan)
    assert usage_capture_wrapper_path(directory).exists()


def test_remove_keep_current_cleans_artifacts_and_preserves_newer_setting(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = {"type": "command", "command": "/usr/local/bin/newer.sh"}
    _write_settings(settings_path, settings)
    settings_before = settings_path.read_bytes()

    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory, keep_current=True)
    assert plan.blocked is None
    assert plan.restore_settings is False
    apply_remove(env, plan)

    assert settings_path.read_bytes() == settings_before
    assert not usage_capture_wrapper_path(directory).exists()
    assert not usage_capture_record_path(directory).exists()


def test_remove_keep_current_aborts_when_settings_repointed_at_wrapper(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # keep-current skips the settings write, but deleting the wrapper and
    # record must still abort if a concurrent edit (reinstall, manual change
    # while the confirm prompt was open) pointed settings back at the wrapper:
    # proceeding would leave Claude invoking a deleted command with the
    # restoration record destroyed.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = {"type": "command", "command": "/usr/local/bin/newer.sh"}
    _write_settings(settings_path, settings)

    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory, keep_current=True)
    assert plan.blocked is None
    assert plan.restore_settings is False
    repointed = json.loads(settings_path.read_text())
    repointed["statusLine"] = {
        "type": "command",
        "command": str(usage_capture_wrapper_path(directory)),
    }
    _write_settings(settings_path, repointed)

    with pytest.raises(SettingsChangedError, match="changed since the plan"):
        apply_remove(env, plan)

    assert usage_capture_wrapper_path(directory).exists()
    assert usage_capture_record_path(directory).exists()
    assert json.loads(settings_path.read_text()) == repointed


def test_remove_keep_current_with_malformed_settings_cleans_artifacts(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # plan_remove documents --keep-current as the cleanup path for malformed
    # settings; the delete-time revalidation must tolerate a file that was and
    # still is malformed (it cannot point at the wrapper).
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings_path.write_text("{not json")

    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory, keep_current=True)
    assert plan.blocked is None
    apply_remove(env, plan)

    assert settings_path.read_text() == "{not json"
    assert not usage_capture_wrapper_path(directory).exists()
    assert not usage_capture_record_path(directory).exists()


def test_remove_keep_current_refused_while_settings_point_at_wrapper(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)

    plan = plan_remove(_env(settings_path, project_dir), directory=directory, keep_current=True)

    assert plan.blocked is not None
    assert "deleted wrapper" in plan.blocked


def test_remove_blocked_when_record_invalid_while_pointing_at_wrapper(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    usage_capture_record_path(directory).write_text("{not json")

    plan = plan_remove(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is not None
    assert "restoration record" in plan.blocked


def test_remove_reports_nothing_to_remove(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _write_settings(settings_path, {"model": "opus"})

    plan = plan_remove(_env(settings_path, project_dir), directory=directory)

    assert plan.nothing_to_remove is True
    assert not plan.changes_anything


# Shell-quoted spellings must be caught by the token pass: the quote breaks the
# raw substring markers, and recording one as the "original renderer" would make
# the wrapper recurse into Dispatch. Truly indirect invocations — a user script
# that calls dispatch internally, `eval` on a built-up string — are NOT
# detectable and acceptably so: the command text carries no marker to inspect,
# writing one requires deliberately hiding the invocation, and the block is a
# best-effort guard rather than a security boundary.
@pytest.mark.parametrize(
    "command",
    [
        "dispatch usage-capture run --provider claude",
        "DISPATCH_HOME=/custom dispatch usage-capture run --provider claude",
        "dispatch-claude-statusline",
        "/bin/sh -c 'dispatch-claude-statusline && ~/bin/renderer.sh'",
        "'/usr/local/bin/dispatch' usage-capture run --provider claude",
        '"dispatch" usage-capture run --provider claude',
        "DISPATCH_HOME='/custom home' dispatch 'usage-capture' run",
        "sh -c \"'dispatch' usage-capture run\"",
        "'dispatch-claude-statusline'",
        "sh -c '\"/opt/bin/dispatch-claude-statusline\"'",
        # The `usage-capture run` argument shape blocks regardless of the
        # invoked binary's identity: an unfamiliar name in front of Dispatch's
        # own capture grammar is either dispatch under an alias (symlink,
        # renamed copy) or masquerading as it, and either would recurse.
        "/usr/local/bin/dx usage-capture run --provider claude",
        "/usr/local/bin/dispatcher usage-capture run",
    ],
)
def test_install_blocked_when_statusline_invokes_capture_entry_point(
    settings_path: Path, project_dir: Path, directory: Path, command: str
) -> None:
    _write_settings(settings_path, {"statusLine": {"type": "command", "command": command}})

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.blocked is not None
    assert "usage-capture entry point" in plan.blocked
    assert "real renderer" in plan.blocked
    assert not plan.changes_anything
    with pytest.raises(RuntimeError):
        apply_install(env, plan)
    assert not usage_capture_wrapper_path(directory).exists()
    assert not usage_capture_record_path(directory).exists()


@pytest.mark.parametrize(
    "command",
    [
        "dispatch usage-capture status",  # not the capture entry point
        "~/bin/dispatch-statusline-theme.sh",  # not the deprecated helper
        "/usr/local/bin/dx render --fast",  # unknown binary without capture args
    ],
)
def test_install_proceeds_for_lookalike_commands(
    settings_path: Path, project_dir: Path, directory: Path, command: str
) -> None:
    # The token pass must not over-block commands that merely resemble a
    # capture entry point; these are legitimate original renderers.
    _write_settings(settings_path, {"statusLine": {"type": "command", "command": command}})

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is None


def test_install_blocked_when_symlink_alias_invokes_capture_run(
    settings_path: Path,
    project_dir: Path,
    directory: Path,
    tmp_path: Path,
    pinned_dispatch_executable: Path,
) -> None:
    # The P1 regression: a symlink named anything (`dx`) pointing at the
    # dispatch executable, invoked with the capture args. A basename-only
    # check records it as the "original renderer" and the wrapper recurses.
    alias = tmp_path / "alias-bin" / "dx"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(pinned_dispatch_executable)
    command = f"{alias} usage-capture run --provider claude"
    _write_settings(settings_path, {"statusLine": {"type": "command", "command": command}})

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is not None
    assert "usage-capture entry point" in plan.blocked


def test_install_blocked_when_symlink_resolves_to_deprecated_helper(
    settings_path: Path, project_dir: Path, directory: Path, tmp_path: Path
) -> None:
    # The deprecated helper takes no arguments, so only symlink resolution can
    # recognize an alias to it — there is no argument shape to match.
    helper = tmp_path / "helpers" / "dispatch-claude-statusline"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    alias = tmp_path / "helpers" / "old-status"
    alias.symlink_to(helper)
    _write_settings(settings_path, {"statusLine": {"type": "command", "command": str(alias)}})

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is not None
    assert "usage-capture entry point" in plan.blocked


def test_install_proceeds_for_dispatch_alias_without_capture_args(
    settings_path: Path,
    project_dir: Path,
    directory: Path,
    tmp_path: Path,
    pinned_dispatch_executable: Path,
) -> None:
    # A symlink to the dispatch executable that does not spell the capture
    # grammar is not a capture entry point — mirrors the allowed
    # `dispatch usage-capture status` lookalike.
    alias = tmp_path / "alias-bin" / "dx"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(pinned_dispatch_executable)
    _write_settings(
        settings_path, {"statusLine": {"type": "command", "command": f"{alias} status"}}
    )

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is None


def test_install_blocked_when_statusline_invokes_wrapper_indirectly(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # points_at() only matches the exact wrapper path; a shell line embedding
    # it would still be recorded as "original" and recurse — block it too.
    wrapper = usage_capture_wrapper_path(directory)
    _write_settings(settings_path, {"statusLine": {"type": "command", "command": f"sh {wrapper}"}})

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is not None
    assert "usage-capture entry point" in plan.blocked


def test_install_proceeds_when_settings_point_exactly_at_wrapper(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # The exact wrapper path is the installed state, not a foreign capture
    # command: repair (missing record) must stay possible.
    _write_settings(
        settings_path,
        {
            "statusLine": {
                "type": "command",
                "command": str(usage_capture_wrapper_path(directory)),
            }
        },
    )

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.blocked is None
    assert plan.write_record is True
    apply_install(env, plan)
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.had_statusline is False


def test_wrapper_bakes_non_default_dispatch_home(
    directory: Path, monkeypatch: pytest.MonkeyPatch, pinned_dispatch_executable: Path
) -> None:
    home = "/custom/dispatch home"  # space forces shell quoting
    monkeypatch.setenv("DISPATCH_HOME", home)
    run_command = _run_command_for(pinned_dispatch_executable)

    content = wrapper_content()

    assert content.startswith("#!/bin/sh\n")
    assert "DISPATCH_HOME='/custom/dispatch home'\n" in content
    assert "export DISPATCH_HOME\n" in content
    assert content.endswith(f"exec {run_command}\n")
    assert installed_command() == f"DISPATCH_HOME='/custom/dispatch home' {run_command}"


def test_wrapper_plain_when_dispatch_home_is_default(
    monkeypatch: pytest.MonkeyPatch, pinned_dispatch_executable: Path
) -> None:
    run_command = _run_command_for(pinned_dispatch_executable)
    plain = f"#!/bin/sh\nexec {run_command}\n"

    monkeypatch.delenv("DISPATCH_HOME", raising=False)
    assert wrapper_content() == plain
    assert installed_command() == run_command

    monkeypatch.setenv("DISPATCH_HOME", "~/.dispatch")
    assert wrapper_content() == plain
    assert installed_command() == run_command


def test_install_records_the_home_baked_wrapper_command(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # The autouse fixture pins DISPATCH_HOME to a temp dir (non-default), so
    # the wrapper and the record must both carry that home.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)

    wrapper_text = usage_capture_wrapper_path(directory).read_text()
    assert "DISPATCH_HOME=" in wrapper_text
    assert "export DISPATCH_HOME\n" in wrapper_text
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.installed_command == installed_command()
    assert record.installed_command.startswith("DISPATCH_HOME=")


def test_apply_install_aborts_when_settings_changed_after_plan(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _write_settings(settings_path, BASE_SETTINGS)
    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)
    concurrent = {**BASE_SETTINGS, "model": "sonnet"}
    _write_settings(settings_path, concurrent)

    with pytest.raises(SettingsChangedError, match="changed since the plan"):
        apply_install(env, plan)

    assert json.loads(settings_path.read_text()) == concurrent
    assert not usage_capture_wrapper_path(directory).exists()
    assert not usage_capture_record_path(directory).exists()


def _prepared_install_plan(
    settings_path: Path, project_dir: Path, directory: Path
) -> tuple[ClaudeStatuslineEnvironment, lifecycle.InstallPlan]:
    """A prepared-state plan: artifacts on disk, settings back at the original."""
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    _write_settings(settings_path, BASE_SETTINGS)
    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)
    assert plan.state == "prepared"
    assert plan.write_settings and not plan.write_wrapper and not plan.write_record
    return env, plan


def test_apply_install_aborts_when_prepared_artifacts_removed_concurrently(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # A concurrent `remove` while the confirm prompt is open deletes wrapper
    # and record without touching settings; applying the prepared plan must
    # abort instead of pointing Claude at a missing wrapper with no record.
    env, plan = _prepared_install_plan(settings_path, project_dir, directory)
    usage_capture_wrapper_path(directory).unlink()
    usage_capture_record_path(directory).unlink()

    with pytest.raises(ArtifactsChangedError, match="changed since"):
        apply_install(env, plan)

    assert json.loads(settings_path.read_text()) == BASE_SETTINGS


def test_apply_install_aborts_when_record_deleted_concurrently(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # Losing only the record is just as unrecoverable: settings would point at
    # the wrapper while remove could never restore the original renderer.
    env, plan = _prepared_install_plan(settings_path, project_dir, directory)
    usage_capture_record_path(directory).unlink()

    with pytest.raises(ArtifactsChangedError, match="changed since"):
        apply_install(env, plan)

    assert json.loads(settings_path.read_text()) == BASE_SETTINGS
    assert usage_capture_wrapper_path(directory).exists()


def test_apply_remove_aborts_when_settings_changed_after_plan(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory)
    concurrent = json.loads(settings_path.read_text())
    concurrent["model"] = "sonnet"
    _write_settings(settings_path, concurrent)

    with pytest.raises(SettingsChangedError, match="changed since the plan"):
        apply_remove(env, plan)

    assert json.loads(settings_path.read_text()) == concurrent
    assert usage_capture_wrapper_path(directory).exists()
    assert usage_capture_record_path(directory).exists()


def test_install_quotes_wrapper_path_needing_shell_quoting(
    settings_path: Path, project_dir: Path, tmp_path: Path
) -> None:
    # A DISPATCH_HOME with a space lands in the wrapper path; Claude executes
    # statusLine.command through a shell, so install must write it quoted —
    # and the quoted form must round-trip through detection and remove.
    directory = tmp_path / "dispatch home" / "claude"
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)

    wrapper = usage_capture_wrapper_path(directory)
    command = json.loads(settings_path.read_text())["statusLine"]["command"]
    assert command == shlex.quote(str(wrapper))
    assert command != str(wrapper)  # the space forced actual quoting
    assert shlex.split(command) == [str(wrapper)]
    assert _state(settings_path, project_dir, directory) == "installed"

    env = _env(settings_path, project_dir)
    apply_remove(env, plan_remove(env, directory=directory))
    assert json.loads(settings_path.read_text())["statusLine"] == ORIGINAL_STATUSLINE


def test_bare_wrapper_path_from_prior_install_still_reads_installed(
    settings_path: Path, project_dir: Path, tmp_path: Path
) -> None:
    # Pre-quoting installs wrote the wrapper path bare into settings; detection
    # must keep reading them as installed, never regress them to drifted.
    directory = tmp_path / "dispatch home" / "claude"
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"]["command"] = str(usage_capture_wrapper_path(directory))
    _write_settings(settings_path, settings)

    assert _state(settings_path, project_dir, directory) == "installed"


@pytest.mark.parametrize(
    "spelling",
    [
        "$DISPATCH_HOME/claude/statusline.sh",
        "$HOME/dispatch-home/claude/statusline.sh",
        "${HOME}/dispatch-home/claude/statusline.sh",
        "~/dispatch-home/claude/statusline.sh",
    ],
)
def test_variable_spelled_wrapper_command_reads_installed(
    settings_path: Path,
    project_dir: Path,
    directory: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spelling: str,
) -> None:
    # Claude executes statusLine.command through a shell, so these spellings
    # all resolve to the managed wrapper; detection must agree instead of
    # misreading installed as drifted. (conftest pins DISPATCH_HOME to
    # tmp_path/"dispatch-home"; HOME is pinned to tmp_path so the $HOME and
    # ~ spellings reach the same file.)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"]["command"] = spelling
    _write_settings(settings_path, settings)

    status = usage_capture_status(
        _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
    )

    assert status.settings_points_at_wrapper is True
    assert status.state == "installed"


def test_install_over_variable_spelled_wrapper_is_repair_not_original(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # A hand-edited $DISPATCH_HOME spelling still invokes the managed wrapper:
    # install must take the idempotent repair path and never record the
    # wrapper spelling as the original renderer.
    _write_settings(
        settings_path,
        {"statusLine": {"type": "command", "command": "$DISPATCH_HOME/claude/statusline.sh"}},
    )

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.blocked is None
    assert plan.write_settings is False  # already points at the wrapper
    apply_install(env, plan)
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.had_statusline is False


def test_remove_restores_original_when_wrapper_spelled_via_variable(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # A variable respelling of the wrapper command still points at the
    # wrapper, so remove restores the recorded original instead of refusing
    # as drifted.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"]["command"] = "$DISPATCH_HOME/claude/statusline.sh"
    _write_settings(settings_path, settings)

    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory)

    assert plan.blocked is None
    assert plan.restore_settings is True
    apply_remove(env, plan)
    assert json.loads(settings_path.read_text())["statusLine"] == ORIGINAL_STATUSLINE
    assert not usage_capture_wrapper_path(directory).exists()


def test_unrelated_home_prefixed_command_is_not_the_wrapper(
    settings_path: Path,
    project_dir: Path,
    directory: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Expansion must not over-match: a different script that merely lives
    # under $HOME (even sharing the wrapper's basename) is a foreign renderer.
    monkeypatch.setenv("HOME", str(tmp_path))
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"]["command"] = "$HOME/bin/statusline.sh"
    _write_settings(settings_path, settings)

    status = usage_capture_status(
        _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
    )

    assert status.settings_points_at_wrapper is False
    assert status.state == "drifted"


def test_install_blocked_when_statusline_embeds_variable_spelled_wrapper(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # The shell-token pass expands variables the same way points_at does, so
    # an indirect invocation of the wrapper via $DISPATCH_HOME cannot be
    # recorded as the original renderer.
    _write_settings(
        settings_path,
        {
            "statusLine": {
                "type": "command",
                "command": "sh $DISPATCH_HOME/claude/statusline.sh",
            }
        },
    )

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is not None
    assert "usage-capture entry point" in plan.blocked


def test_install_blocked_when_statusline_embeds_quoted_wrapper(
    settings_path: Path, project_dir: Path, tmp_path: Path
) -> None:
    # The capture-entry-point block must also catch the shell-quoted wrapper
    # path embedded in a larger command line.
    directory = tmp_path / "dispatch home" / "claude"
    wrapper = usage_capture_wrapper_path(directory)
    _write_settings(
        settings_path,
        {"statusLine": {"type": "command", "command": f"sh {shlex.quote(str(wrapper))}"}},
    )

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is not None
    assert "usage-capture entry point" in plan.blocked


def _wrapper_alias(kind: str, directory: Path, tmp_path: Path) -> str:
    """A spelling of the wrapper path that only resolution can recognize."""
    wrapper = usage_capture_wrapper_path(directory)
    if kind == "file-symlink":
        alias = tmp_path / "wrapper-alias.sh"
        alias.symlink_to(wrapper)
        return str(alias)
    if kind == "dotdot":
        return str(directory / ".." / directory.name / wrapper.name)
    linked_home = tmp_path / "linked-dispatch-home"
    linked_home.symlink_to(directory.parent)
    return str(linked_home / directory.name / wrapper.name)


_ALIAS_KINDS = ["file-symlink", "dotdot", "dir-symlink"]


@pytest.mark.parametrize("kind", _ALIAS_KINDS)
def test_alias_spelled_wrapper_command_reads_installed(
    settings_path: Path, project_dir: Path, directory: Path, tmp_path: Path, kind: str
) -> None:
    # A statusLine.command reaching the wrapper via a symlink, a symlinked
    # parent directory, or a ..-containing path still invokes the managed
    # wrapper; detection must read installed, not drifted.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"]["command"] = _wrapper_alias(kind, directory, tmp_path)
    _write_settings(settings_path, settings)

    status = usage_capture_status(
        _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
    )

    assert status.settings_points_at_wrapper is True
    assert status.state == "installed"


@pytest.mark.parametrize("kind", _ALIAS_KINDS)
def test_install_over_alias_spelled_wrapper_is_repair_not_original(
    settings_path: Path, project_dir: Path, directory: Path, tmp_path: Path, kind: str
) -> None:
    # An alias spelling of the wrapper must take the idempotent repair path —
    # recording it as the "original renderer" would make restoration point the
    # statusline back at the wrapper it is supposed to replace. (Pre-install
    # the symlink aliases dangle; resolution still follows their link text.)
    alias = _wrapper_alias(kind, directory, tmp_path)
    _write_settings(settings_path, {"statusLine": {"type": "command", "command": alias}})

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.blocked is None
    assert plan.write_settings is False  # already points at the wrapper
    apply_install(env, plan)
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert record.had_statusline is False


@pytest.mark.parametrize("kind", _ALIAS_KINDS)
def test_install_blocked_when_statusline_embeds_alias_spelled_wrapper(
    settings_path: Path, project_dir: Path, directory: Path, tmp_path: Path, kind: str
) -> None:
    # The shell-token pass resolves aliases the same way points_at does, so an
    # indirect invocation of the wrapper via a symlink or ..-containing token
    # cannot be recorded as the original renderer.
    alias = _wrapper_alias(kind, directory, tmp_path)
    _write_settings(
        settings_path,
        {"statusLine": {"type": "command", "command": f"sh {shlex.quote(alias)}"}},
    )

    plan = plan_install(_env(settings_path, project_dir), directory=directory)

    assert plan.blocked is not None
    assert "usage-capture entry point" in plan.blocked


def test_wrapper_under_symlinked_directory_matches_real_path_command(
    settings_path: Path, project_dir: Path, tmp_path: Path
) -> None:
    # The reverse direction: the managed wrapper path itself may contain a
    # symlinked segment (DISPATCH_HOME under a symlinked homedir, macOS /tmp).
    # Both sides resolve, so a command spelling the *real* path still matches.
    real_home = tmp_path / "real-dispatch-home"
    real_home.mkdir()
    linked_home = tmp_path / "linked-real-home"
    linked_home.symlink_to(real_home)
    directory = linked_home / "claude"
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"]["command"] = str(real_home / "claude" / "statusline.sh")
    _write_settings(settings_path, settings)

    assert _state(settings_path, project_dir, directory) == "installed"


def test_install_and_remove_preserve_symlinked_settings(
    settings_path: Path, project_dir: Path, directory: Path, tmp_path: Path
) -> None:
    # Dotfiles-managed setups symlink settings.json elsewhere; writes must land
    # on the target and the symlink itself must survive install and remove.
    target = tmp_path / "dotfiles" / "claude-settings.json"
    _write_settings(target, BASE_SETTINGS)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.symlink_to(target)

    _install(settings_path, project_dir, directory)

    assert settings_path.is_symlink()
    wrapper = usage_capture_wrapper_path(directory)
    assert json.loads(target.read_text())["statusLine"]["command"] == shlex.quote(str(wrapper))
    assert _state(settings_path, project_dir, directory) == "installed"

    env = _env(settings_path, project_dir)
    apply_remove(env, plan_remove(env, directory=directory))

    assert settings_path.is_symlink()
    assert json.loads(target.read_text())["statusLine"] == ORIGINAL_STATUSLINE


def test_install_through_dangling_symlink_creates_the_target(
    settings_path: Path, project_dir: Path, directory: Path, tmp_path: Path
) -> None:
    # A dangling symlink reads as absent settings; the write lands on the path
    # the link names, which also makes the link valid afterwards.
    target = tmp_path / "dotfiles" / "claude-settings.json"
    target.parent.mkdir(parents=True)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.symlink_to(target)

    _install(settings_path, project_dir, directory)

    assert settings_path.is_symlink()
    assert target.is_file()
    command = json.loads(target.read_text())["statusLine"]["command"]
    assert shlex.split(command) == [str(usage_capture_wrapper_path(directory))]
    assert _state(settings_path, project_dir, directory) == "installed"


def test_apply_install_aborts_when_symlink_target_changed_after_plan(
    settings_path: Path, project_dir: Path, directory: Path, tmp_path: Path
) -> None:
    # TOCTOU revalidation must see concurrent edits made through the symlink's
    # target, not just direct writes to the link path.
    target = tmp_path / "dotfiles" / "claude-settings.json"
    _write_settings(target, BASE_SETTINGS)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.symlink_to(target)
    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)
    concurrent = {**BASE_SETTINGS, "model": "sonnet"}
    _write_settings(target, concurrent)

    with pytest.raises(SettingsChangedError, match="changed since the plan"):
        apply_install(env, plan)

    assert settings_path.is_symlink()
    assert json.loads(target.read_text()) == concurrent
    assert not usage_capture_wrapper_path(directory).exists()


def _raise_os_error(*args: object, **kwargs: object) -> None:
    raise OSError("simulated crash")


def test_crash_between_record_and_wrapper_is_recoverable(
    settings_path: Path, project_dir: Path, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings(settings_path, BASE_SETTINGS)
    monkeypatch.setattr(lifecycle, "write_private_file", _raise_os_error, raising=True)
    env = _env(settings_path, project_dir)
    with pytest.raises(OSError):
        apply_install(env, plan_install(env, directory=directory, dispatch_on_path=True))
    monkeypatch.undo()

    # Record landed first; wrapper and settings are untouched.
    assert usage_capture_record_path(directory).exists()
    assert not usage_capture_wrapper_path(directory).exists()
    assert json.loads(settings_path.read_text())["statusLine"] == ORIGINAL_STATUSLINE

    _install(settings_path, project_dir, directory)
    assert _state(settings_path, project_dir, directory) == "installed"


# --- durable executable baking (wrapper must not depend on Claude's PATH) ---


def test_install_bakes_absolute_dispatch_executable_into_wrapper_and_record(
    settings_path: Path, project_dir: Path, directory: Path, pinned_dispatch_executable: Path
) -> None:
    # `uv run dispatch usage-capture install` passes the PATH preflight via a
    # transient venv shim, but Claude later invokes the wrapper under its own
    # environment: a bare `exec dispatch ...` would fail there. The wrapper
    # must carry the absolute executable resolved at install time.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)

    wrapper_text = usage_capture_wrapper_path(directory).read_text()
    exec_tokens = shlex.split(wrapper_text.splitlines()[-1])
    assert exec_tokens[0] == "exec"
    assert exec_tokens[1] == str(pinned_dispatch_executable)
    assert Path(exec_tokens[1]).is_absolute()
    record = read_usage_capture_record(path=usage_capture_record_path(directory))
    assert record is not None
    assert str(pinned_dispatch_executable) in record.installed_command


def test_wrapper_falls_back_to_bare_dispatch_when_nothing_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lifecycle, "_resolve_dispatch_executable", lambda: None)

    assert wrapper_content().endswith(f"exec {RUN_COMMAND}\n")
    assert installed_command().endswith(RUN_COMMAND)


def test_resolver_prefers_argv0_then_path_then_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv_exe = tmp_path / "venv" / "bin" / "dispatch"
    path_exe = tmp_path / "tools" / "dispatch"
    for exe in (argv_exe, path_exe):
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_exe.parent))

    monkeypatch.setattr(sys, "argv", [str(argv_exe)])
    assert _REAL_RESOLVE() == argv_exe.resolve()

    monkeypatch.setattr(sys, "argv", ["pytest"])
    assert _REAL_RESOLVE() == path_exe.resolve()

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert _REAL_RESOLVE() is None


def test_legacy_bare_dispatch_wrapper_reads_installed_and_refreshes_on_reinstall(
    settings_path: Path, project_dir: Path, directory: Path, pinned_dispatch_executable: Path
) -> None:
    # Pre-baking installs wrote `exec dispatch ...` with no absolute path.
    # Status must keep reporting them installed (never broken/drifted), and a
    # reinstall must silently rebake the wrapper with the resolved executable.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    wrapper = usage_capture_wrapper_path(directory)
    home = str(directory.parent)  # the effective DISPATCH_HOME (pinned by conftest)
    legacy = (
        f"#!/bin/sh\nDISPATCH_HOME={shlex.quote(home)}\nexport DISPATCH_HOME\nexec {RUN_COMMAND}\n"
    )
    wrapper.write_text(legacy)

    assert _state(settings_path, project_dir, directory) == "installed"

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)
    assert plan.write_wrapper is True
    assert plan.write_record is False
    assert plan.write_settings is False
    apply_install(env, plan)
    assert str(pinned_dispatch_executable) in wrapper.read_text()
    assert _state(settings_path, project_dir, directory) == "installed"


def test_wrapper_baked_by_another_environment_still_reads_installed(
    settings_path: Path,
    project_dir: Path,
    directory: Path,
    tmp_path: Path,
    pinned_dispatch_executable: Path,
) -> None:
    # A wrapper whose baked executable differs from what this environment
    # resolves (install ran elsewhere) is still a working install, as long as
    # that executable actually exists and is executable.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    other_executable = tmp_path / "other-env" / "bin" / "dispatch"
    other_executable.parent.mkdir(parents=True)
    other_executable.write_text("#!/bin/sh\n")
    other_executable.chmod(0o755)
    wrapper = usage_capture_wrapper_path(directory)
    other = wrapper_content().replace(str(pinned_dispatch_executable), str(other_executable))
    assert other != wrapper_content()
    wrapper.write_text(other)

    assert _state(settings_path, project_dir, directory) == "installed"
    status = usage_capture_status(
        _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
    )
    assert status.wrapper_current is True
    assert status.wrapper_missing_executable is None


def test_wrapper_with_missing_baked_executable_is_broken_and_reinstall_repairs(
    settings_path: Path,
    project_dir: Path,
    directory: Path,
    tmp_path: Path,
    pinned_dispatch_executable: Path,
) -> None:
    # A baked absolute executable that was later deleted or moved (venv
    # rebuilt, cache cleaned) fails at exec on every statusline refresh, so
    # the wrapper must read broken — not installed — and status must name the
    # missing executable. Reinstall rebakes the currently-resolved executable.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    wrapper = usage_capture_wrapper_path(directory)
    missing = tmp_path / "rebuilt-venv" / "bin" / "dispatch"  # never created
    wrapper.write_text(wrapper_content().replace(str(pinned_dispatch_executable), str(missing)))

    assert _state(settings_path, project_dir, directory) == "broken"
    status = usage_capture_status(
        _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
    )
    assert status.wrapper_current is False
    assert status.wrapper_missing_executable == str(missing)

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)
    assert plan.blocked is None
    assert plan.write_wrapper is True
    assert plan.write_record is False
    assert plan.write_settings is False
    apply_install(env, plan)
    assert str(pinned_dispatch_executable) in wrapper.read_text()
    assert _state(settings_path, project_dir, directory) == "installed"


def test_wrapper_with_non_executable_baked_executable_is_broken(
    settings_path: Path, project_dir: Path, directory: Path, pinned_dispatch_executable: Path
) -> None:
    # Present but no longer executable fails exec the same way as deleted.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    pinned_dispatch_executable.chmod(0o644)

    status = usage_capture_status(
        _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
    )

    assert status.state == "broken"
    assert status.wrapper_missing_executable == str(pinned_dispatch_executable)


def test_warns_when_dispatch_resolves_to_ephemeral_location(
    settings_path: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings(settings_path, {"model": "opus"})
    monkeypatch.setattr(
        lifecycle,
        "_resolve_dispatch_executable",
        lambda: Path.home() / ".cache" / "uv" / "archive-v0" / "abc" / "bin" / "dispatch",
    )

    warnings = environment_warnings(_env(settings_path, project_dir), dispatch_on_path=True)

    assert any("cache or temp" in warning for warning in warnings)


# --- non-object statusLine values must be preserved, never clobbered ---


@pytest.mark.parametrize("value", ["~/bin/renderer.sh", ["cmd", "arg"], 42, None, True])
def test_install_blocked_on_non_object_statusline_value(
    settings_path: Path, project_dir: Path, directory: Path, value: object
) -> None:
    # A present-but-non-object statusLine must not read as "absent": install
    # would record had_statusline=False, overwrite the value, and remove would
    # then delete the key — irreversible loss of the user's setting.
    _write_settings(settings_path, {"model": "opus", "statusLine": value})
    before = settings_path.read_bytes()

    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)

    assert plan.blocked is not None
    assert "non-object" in plan.blocked
    assert not plan.changes_anything
    with pytest.raises(RuntimeError):
        apply_install(env, plan)
    assert settings_path.read_bytes() == before
    assert not usage_capture_wrapper_path(directory).exists()
    assert not usage_capture_record_path(directory).exists()


def test_remove_refuses_to_touch_non_object_statusline_it_cannot_restore(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # Installed with no original renderer, then hand-edited to a string: the
    # value matches nothing the record can restore, so default remove must
    # refuse (not silently proceed as "prepared") and keep-current must leave
    # the value untouched.
    _install(settings_path, project_dir, directory, yes_settings={"model": "opus"})
    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = "~/bin/hand-edited.sh"
    _write_settings(settings_path, settings)

    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory)
    assert plan.blocked is not None
    assert "--keep-current" in plan.blocked

    kept = plan_remove(env, directory=directory, keep_current=True)
    assert kept.blocked is None
    apply_remove(env, kept)
    assert json.loads(settings_path.read_text())["statusLine"] == "~/bin/hand-edited.sh"
    assert not usage_capture_wrapper_path(directory).exists()


def test_status_reports_non_object_statusline_distinctly_as_drift(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings = json.loads(settings_path.read_text())
    settings["statusLine"] = ["not", "an", "object"]
    _write_settings(settings_path, settings)

    status = usage_capture_status(
        _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
    )

    assert status.state == "drifted"
    assert status.settings_statusline_unsupported is True
    assert status.settings_malformed is False


def test_environment_distinguishes_unsupported_from_absent_statusline(
    settings_path: Path, project_dir: Path
) -> None:
    _write_settings(settings_path, {"model": "opus"})
    assert _env(settings_path, project_dir).statusline_unsupported is False

    _write_settings(settings_path, {"statusLine": {"type": "command", "command": "x"}})
    assert _env(settings_path, project_dir).statusline_unsupported is False

    _write_settings(settings_path, {"statusLine": None})
    env = _env(settings_path, project_dir)
    assert env.statusline_unsupported is True
    assert env.statusline is None


# --- relative DISPATCH_HOME must never leak into persisted artifacts ---


def test_relative_dispatch_home_persists_absolute_wrapper_and_home(
    settings_path: Path, project_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A relative DISPATCH_HOME is cwd-dependent; Claude invokes statuslines
    # from arbitrary cwds, so both the wrapper path written to settings and
    # the DISPATCH_HOME baked into the wrapper must be anchored to absolute
    # paths at install time.
    install_cwd = tmp_path / "install-cwd"
    install_cwd.mkdir()
    monkeypatch.chdir(install_cwd)
    monkeypatch.setenv("DISPATCH_HOME", "relative-home")
    _write_settings(settings_path, BASE_SETTINGS)

    env = _env(settings_path, project_dir)
    plan = plan_install(env, dispatch_on_path=True)  # directory=None: config seam
    apply_install(env, plan)

    expected_home = install_cwd / "relative-home"
    wrapper = expected_home / "claude" / "statusline.sh"
    assert wrapper.is_file()
    command = json.loads(settings_path.read_text())["statusLine"]["command"]
    (command_path,) = shlex.split(command)
    assert Path(command_path).is_absolute()
    assert Path(command_path) == wrapper
    wrapper_text = wrapper.read_text()
    assert f"DISPATCH_HOME={shlex.quote(str(expected_home))}\n" in wrapper_text
    assert "DISPATCH_HOME=relative-home" not in wrapper_text

    # From any other cwd, an absolute DISPATCH_HOME (as the wrapper bakes it)
    # still sees the same install.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("DISPATCH_HOME", str(expected_home))
    status = usage_capture_status(_env(settings_path, project_dir), dispatch_on_path=True)
    assert status.state == "installed"


# --- unreadable settings are not malformed: cleanup must not proceed blind ---

_not_as_root = pytest.mark.skipif(os.geteuid() == 0, reason="chmod 000 does not deny reads to root")


@_not_as_root
def test_install_blocked_on_unreadable_settings(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # A PermissionError is not malformed JSON: the content exists but cannot
    # be inspected, so install must block with the fix in hand rather than
    # rebuild the file blind.
    _write_settings(settings_path, BASE_SETTINGS)
    settings_path.chmod(0)
    try:
        env = _env(settings_path, project_dir)
        assert env.settings_unreadable is True
        assert env.settings_malformed is False
        plan = plan_install(env, directory=directory, dispatch_on_path=True)
        assert plan.blocked is not None
        assert "cannot be read" in plan.blocked
        assert "permission" in plan.blocked
        assert not plan.changes_anything
        with pytest.raises(RuntimeError):
            apply_install(env, plan)
        assert not usage_capture_wrapper_path(directory).exists()
        assert not usage_capture_record_path(directory).exists()
    finally:
        settings_path.chmod(0o600)


@_not_as_root
@pytest.mark.parametrize("keep_current", [False, True])
def test_remove_blocked_on_unreadable_settings_in_both_modes(
    settings_path: Path, project_dir: Path, directory: Path, keep_current: bool
) -> None:
    # Unreadable settings may still point at the wrapper (unreadable is not
    # provably-not-pointing malformed), so even --keep-current must refuse to
    # delete the wrapper and restoration record.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings_path.chmod(0)
    try:
        env = _env(settings_path, project_dir)
        plan = plan_remove(env, directory=directory, keep_current=keep_current)
        assert plan.blocked is not None
        assert "cannot be read" in plan.blocked
        with pytest.raises(RuntimeError):
            apply_remove(env, plan)
    finally:
        settings_path.chmod(0o600)
    assert usage_capture_wrapper_path(directory).exists()
    assert usage_capture_record_path(directory).exists()
    assert json.loads(settings_path.read_text())["statusLine"]["command"] == shlex.quote(
        str(usage_capture_wrapper_path(directory))
    )


@_not_as_root
def test_status_reports_unreadable_settings_distinct_from_malformed(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    settings_path.chmod(0)
    try:
        status = usage_capture_status(
            _env(settings_path, project_dir), directory=directory, dispatch_on_path=True
        )
    finally:
        settings_path.chmod(0o600)

    assert status.state == "broken"
    assert status.settings_unreadable is True
    assert status.settings_malformed is False


@_not_as_root
def test_apply_remove_aborts_when_settings_become_unreadable_after_plan(
    settings_path: Path, project_dir: Path, directory: Path
) -> None:
    # The apply-time revalidation must never let an unreadable file pass the
    # two-malformed-reads carve-out: unreadable content cannot be proven safe.
    _install(settings_path, project_dir, directory, yes_settings=BASE_SETTINGS)
    env = _env(settings_path, project_dir)
    plan = plan_remove(env, directory=directory)
    settings_path.chmod(0)
    try:
        with pytest.raises(SettingsChangedError, match="unreadable"):
            apply_remove(env, plan)
    finally:
        settings_path.chmod(0o600)
    assert usage_capture_wrapper_path(directory).exists()
    assert usage_capture_record_path(directory).exists()


# --- concurrent settings writes during the artifact writes must not be lost ---


def test_apply_install_aborts_when_settings_change_during_artifact_writes(
    settings_path: Path, project_dir: Path, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The up-front revalidation leaves a window while the record and wrapper
    # are written (and fsynced); a concurrent settings writer landing there
    # must abort the settings replacement rather than be clobbered by the
    # rebuild-from-snapshot. Record+wrapper without settings is the documented
    # `prepared` state, recoverable by rerunning install.
    _write_settings(settings_path, BASE_SETTINGS)
    env = _env(settings_path, project_dir)
    plan = plan_install(env, directory=directory, dispatch_on_path=True)
    concurrent = {**BASE_SETTINGS, "model": "sonnet"}

    def wrapper_write_races_a_settings_edit(path: Path, data: bytes, *, mode: int = 0o600) -> None:
        write_private_file(path, data, mode=mode)
        _write_settings(settings_path, concurrent)

    monkeypatch.setattr(lifecycle, "write_private_file", wrapper_write_races_a_settings_edit)
    with pytest.raises(SettingsChangedError, match="changed since the plan"):
        apply_install(env, plan)
    monkeypatch.setattr(lifecycle, "write_private_file", write_private_file)

    assert json.loads(settings_path.read_text()) == concurrent  # edit survived
    assert usage_capture_wrapper_path(directory).exists()
    assert usage_capture_record_path(directory).exists()
    assert _state(settings_path, project_dir, directory) == "prepared"

    _install(settings_path, project_dir, directory)  # rerun completes the install
    assert _state(settings_path, project_dir, directory) == "installed"


def test_crash_before_settings_write_is_recoverable_and_removable(
    settings_path: Path, project_dir: Path, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings(settings_path, BASE_SETTINGS)
    monkeypatch.setattr(lifecycle, "write_claude_settings", _raise_os_error, raising=True)
    env = _env(settings_path, project_dir)
    with pytest.raises(OSError):
        apply_install(env, plan_install(env, directory=directory, dispatch_on_path=True))
    monkeypatch.undo()

    # Re-running install completes; the stale record still holds the original.
    _install(settings_path, project_dir, directory)
    assert _state(settings_path, project_dir, directory) == "installed"
    env = _env(settings_path, project_dir)
    apply_remove(env, plan_remove(env, directory=directory))
    assert json.loads(settings_path.read_text())["statusLine"] == ORIGINAL_STATUSLINE
