"""Phase 0 smoke tests: the console entrypoints resolve and render help.

These prove the scaffold is installable and wired. Real behavior is tested from
Phase 1 onward (client integration) and Phase 2 (examples-as-tests).
"""

from __future__ import annotations

from typer.testing import CliRunner

from outfitter.dispatch.cli import app as cli_app
from outfitter.dispatch.daemon.__main__ import app as daemon_app

runner = CliRunner()


def test_cli_help_renders() -> None:
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "dispatch" in result.output


def test_cli_version_renders() -> None:
    result = runner.invoke(cli_app, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("dispatch ")


def test_cli_completion_command_prints_script() -> None:
    result = runner.invoke(cli_app, ["completion", "bash"])
    assert result.exit_code == 0, result.output
    assert "_DISPATCH_COMPLETE" in result.output
    assert "dispatch" in result.output


def test_daemon_help_renders() -> None:
    result = runner.invoke(daemon_app, ["--help"])
    assert result.exit_code == 0
    assert "dispatchd" in result.output


def test_daemon_version_renders() -> None:
    result = runner.invoke(daemon_app, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("dispatchd ")
