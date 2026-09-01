"""`dispatch usage-capture` is a daemon-free surface control (never an op)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from outfitter.dispatch.core.claude_statusline import read_claude_statusline_snapshot
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
