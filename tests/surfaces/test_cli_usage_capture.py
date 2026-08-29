"""`dispatch usage-capture` is a daemon-free surface control (never an op)."""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

import pytest
from typer.testing import CliRunner

from outfitter.dispatch.core.claude_statusline import read_claude_statusline_snapshot
from outfitter.dispatch.core.usage_capture import (
    usage_capture_record_path,
    usage_capture_wrapper_path,
)
from outfitter.dispatch.core.usage_capture_settings import claude_settings_path
from outfitter.dispatch.surfaces.cli import build_cli

runner = CliRunner()

_ANSI_ESCAPES = re.compile(r"\x1b\[[0-9;]*m")

# Rich wraps help text at the terminal width (folding on hyphens, so tokens
# like `--provider` can split across lines on a narrow CI terminal). Pin a
# wide terminal so assertions on option names are deterministic everywhere.
_WIDE_TERMINAL = {"COLUMNS": "200", "LINES": "50"}


def _help_output(args: list[str]) -> str:
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))
    result = runner.invoke(app, args, env=_WIDE_TERMINAL)
    assert result.exit_code == 0
    return _ANSI_ESCAPES.sub("", result.output)


_ORIGINAL_STATUSLINE = {"type": "command", "command": "~/bin/original.sh", "padding": 0}


def _write_claude_settings(data: dict[str, object]) -> Path:
    # claude_settings_path() resolves through CLAUDE_CONFIG_DIR, which the
    # autouse isolated_dispatch_home fixture pins to a temp dir.
    path = claude_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def test_usage_capture_group_help_renders() -> None:
    output = _help_output(["usage-capture", "--help"])

    assert "daemon-free" in output
    assert "run" in output


def test_usage_capture_run_help_renders() -> None:
    output = _help_output(["usage-capture", "run", "--help"])

    assert "--provider" in output


def test_usage_capture_run_captures_from_stdin_without_daemon_or_output() -> None:
    # The autouse isolated_dispatch_home fixture points DISPATCH_HOME at a temp
    # dir, and the socket path is a dead socket: reaching the daemon would fail
    # loudly, so a clean exit proves the run path stayed local.
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))
    payload = json.dumps(
        {
            "session_id": "raw-session-id",
            "rate_limits": {"five_hour": {"used_percentage": 12.5, "resets_at": 1_738_425_600}},
        }
    )

    result = runner.invoke(app, ["usage-capture", "run", "--provider", "claude"], input=payload)

    assert result.exit_code == 0
    assert result.output == ""
    snapshot = read_claude_statusline_snapshot()
    assert snapshot is not None
    assert snapshot.rate_limits.five_hour is not None
    assert snapshot.rate_limits.five_hour.used_percentage == 12.5


def test_lifecycle_commands_render_help() -> None:
    for command in ("install", "status", "remove"):
        output = _help_output(["usage-capture", command, "--help"])
        assert "--provider" in output
    assert "--keep-current" in _help_output(["usage-capture", "remove", "--help"])


def test_install_requires_yes_when_stdin_is_not_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _write_claude_settings({"statusLine": _ORIGINAL_STATUSLINE})
    before = settings.read_bytes()
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))

    result = runner.invoke(app, ["usage-capture", "install", "--provider", "claude"])

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    assert settings.read_bytes() == before


def test_install_dry_run_makes_zero_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _write_claude_settings({"statusLine": _ORIGINAL_STATUSLINE})
    before = settings.read_bytes()
    mtime = settings.stat().st_mtime_ns
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))

    result = runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--dry-run"])

    assert result.exit_code == 0
    assert "dry run" in result.output
    assert settings.read_bytes() == before
    assert settings.stat().st_mtime_ns == mtime
    assert not usage_capture_wrapper_path().exists()
    assert not usage_capture_record_path().exists()


def test_install_status_remove_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _write_claude_settings({"model": "opus", "statusLine": _ORIGINAL_STATUSLINE})
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))

    install = runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"])
    assert install.exit_code == 0
    live = json.loads(settings.read_text())
    assert live["statusLine"]["command"] == str(usage_capture_wrapper_path())
    assert live["statusLine"]["padding"] == 0

    status = runner.invoke(app, ["usage-capture", "status", "--provider", "claude", "--json"])
    assert status.exit_code == 0
    payload = json.loads(status.output)
    assert payload["state"] == "installed"
    assert payload["original_renderer_recorded"] is True
    assert "original.sh" not in status.output

    human = runner.invoke(app, ["usage-capture", "status", "--provider", "claude"])
    assert human.exit_code == 0
    assert "state:" in human.output
    assert "original.sh" not in human.output

    remove = runner.invoke(app, ["usage-capture", "remove", "--provider", "claude", "--yes"])
    assert remove.exit_code == 0
    restored = json.loads(settings.read_text())
    assert restored["statusLine"] == _ORIGINAL_STATUSLINE
    assert restored["model"] == "opus"
    assert not usage_capture_wrapper_path().exists()
    assert not usage_capture_record_path().exists()


def test_remove_dry_run_makes_zero_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _write_claude_settings({"statusLine": _ORIGINAL_STATUSLINE})
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))
    assert (
        runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"]).exit_code
        == 0
    )
    settings_before = settings.read_bytes()
    wrapper_before = usage_capture_wrapper_path().read_bytes()

    result = runner.invoke(app, ["usage-capture", "remove", "--provider", "claude", "--dry-run"])

    assert result.exit_code == 0
    assert "dry run" in result.output
    assert settings.read_bytes() == settings_before
    assert usage_capture_wrapper_path().read_bytes() == wrapper_before
    assert usage_capture_record_path().exists()


def test_install_refuses_drift_then_succeeds_after_keep_current_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _write_claude_settings({"statusLine": _ORIGINAL_STATUSLINE})
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))
    assert (
        runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"]).exit_code
        == 0
    )
    newer = {"type": "command", "command": "/usr/local/bin/newer.sh"}
    settings.write_text(json.dumps({"statusLine": newer}, indent=2) + "\n")
    record_before = usage_capture_record_path().read_bytes()
    settings_before = settings.read_bytes()

    refused = runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"])
    assert refused.exit_code == 1
    assert "--keep-current" in refused.stderr
    assert usage_capture_record_path().read_bytes() == record_before
    assert settings.read_bytes() == settings_before

    cleaned = runner.invoke(
        app, ["usage-capture", "remove", "--provider", "claude", "--yes", "--keep-current"]
    )
    assert cleaned.exit_code == 0

    reinstalled = runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"])
    assert reinstalled.exit_code == 0
    live = json.loads(settings.read_text())
    assert live["statusLine"]["command"] == str(usage_capture_wrapper_path())
    status = runner.invoke(app, ["usage-capture", "status", "--provider", "claude", "--json"])
    assert json.loads(status.output)["state"] == "installed"


def test_install_refuses_statusline_that_already_invokes_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    capture_command = "dispatch usage-capture run --provider claude"
    settings = _write_claude_settings(
        {"statusLine": {"type": "command", "command": capture_command}}
    )
    before = settings.read_bytes()
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))

    result = runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"])

    assert result.exit_code == 1
    assert "usage-capture entry point" in result.stderr
    assert "real renderer" in result.stderr
    assert settings.read_bytes() == before
    assert not usage_capture_wrapper_path().exists()
    assert not usage_capture_record_path().exists()


def test_install_refuses_non_object_statusline_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hand-edited string statusLine cannot be preserved in the restoration
    # record; install must refuse instead of overwriting it irreversibly.
    monkeypatch.chdir(tmp_path)
    settings = _write_claude_settings({"statusLine": "~/bin/hand-edited.sh"})
    before = settings.read_bytes()
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))

    result = runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"])

    assert result.exit_code == 1
    assert "non-object" in result.stderr
    assert settings.read_bytes() == before
    assert not usage_capture_wrapper_path().exists()
    assert not usage_capture_record_path().exists()


def test_install_writes_wrapper_that_bakes_the_selected_dispatch_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The autouse isolated_dispatch_home fixture pins DISPATCH_HOME to a
    # non-default temp dir; the wrapper Claude invokes must carry it so run
    # finds the restoration record in the same home install used.
    monkeypatch.chdir(tmp_path)
    _write_claude_settings({"statusLine": _ORIGINAL_STATUSLINE})
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))

    result = runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"])

    assert result.exit_code == 0
    wrapper_text = usage_capture_wrapper_path().read_text()
    assert "DISPATCH_HOME=" in wrapper_text
    assert "export DISPATCH_HOME" in wrapper_text
    # The exec line bakes whatever dispatch executable install resolved (an
    # absolute path in a real environment, bare `dispatch` when unresolvable);
    # assert the shape rather than pinning the environment-dependent path.
    exec_tokens = shlex.split(wrapper_text.rstrip().splitlines()[-1])
    assert exec_tokens[0] == "exec"
    assert Path(exec_tokens[1]).name == "dispatch"
    assert exec_tokens[2:] == ["usage-capture", "run", "--provider", "claude"]


@pytest.mark.skipif(os.geteuid() == 0, reason="chmod 000 does not deny reads to root")
def test_remove_keep_current_refuses_unreadable_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unreadable settings file may still point at the wrapper; even the
    # --keep-current cleanup path must refuse to delete the artifacts until
    # the file can be inspected again.
    monkeypatch.chdir(tmp_path)
    settings = _write_claude_settings({"statusLine": _ORIGINAL_STATUSLINE})
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))
    assert (
        runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"]).exit_code
        == 0
    )
    settings.chmod(0)
    try:
        result = runner.invoke(
            app,
            ["usage-capture", "remove", "--provider", "claude", "--yes", "--keep-current"],
        )
    finally:
        settings.chmod(0o600)

    assert result.exit_code == 1
    assert "cannot be read" in result.stderr
    assert usage_capture_wrapper_path().exists()
    assert usage_capture_record_path().exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="chmod 500 does not deny writes to root")
def test_install_projects_unwritable_artifact_dir_as_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Lifecycle I/O failures (ensure_private_dir, write_private_file,
    # write_claude_settings) must be projected at the CLI boundary as a clean
    # exit-1 stderr line naming the failing path — never a traceback.
    monkeypatch.chdir(tmp_path)
    _write_claude_settings({"statusLine": _ORIGINAL_STATUSLINE})
    dispatch_home = Path(os.environ["DISPATCH_HOME"])
    dispatch_home.mkdir(parents=True, exist_ok=True)
    dispatch_home.chmod(0o500)
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))
    try:
        result = runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"])
    finally:
        dispatch_home.chmod(0o700)

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert str(dispatch_home / "claude") in result.stderr
    assert "check file and directory permissions" in result.stderr
    assert "Traceback" not in result.stderr


def test_remove_refuses_drift_and_keep_current_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _write_claude_settings({"statusLine": _ORIGINAL_STATUSLINE})
    app = build_cli(socket_path=Path("/tmp/dispatch-usage-capture-test.sock"))
    assert (
        runner.invoke(app, ["usage-capture", "install", "--provider", "claude", "--yes"]).exit_code
        == 0
    )
    drifted = {"statusLine": {"type": "command", "command": "/usr/local/bin/newer.sh"}}
    settings.write_text(json.dumps(drifted, indent=2) + "\n")

    refused = runner.invoke(app, ["usage-capture", "remove", "--provider", "claude", "--yes"])
    assert refused.exit_code == 1
    assert "--keep-current" in refused.stderr
    assert usage_capture_wrapper_path().exists()

    cleaned = runner.invoke(
        app,
        ["usage-capture", "remove", "--provider", "claude", "--yes", "--keep-current"],
    )
    assert cleaned.exit_code == 0
    assert json.loads(settings.read_text()) == drifted
    assert not usage_capture_wrapper_path().exists()
    assert not usage_capture_record_path().exists()
