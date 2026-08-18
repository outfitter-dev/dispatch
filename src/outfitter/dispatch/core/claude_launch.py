"""Internal Claude background-session launch and argv projection."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence

from outfitter.dispatch.core.claude_launch_types import (
    ClaudeLaunchAmbiguousError,
    ClaudeLaunchArgvLimitError,
    ClaudeLaunchCommandError,
    ClaudeLaunchEnvelope,
    ClaudeLaunchError,
    ClaudeLaunchIndeterminateError,
    ClaudeLaunchObservation,
    ClaudeLaunchOutputError,
    ClaudeLaunchOutputLimitError,
    ClaudeLaunchTimeoutError,
    ClaudeLaunchValidationError,
    ClaudeProcessResult,
    ClaudeProcessRunner,
)
from outfitter.dispatch.core.claude_process import run_claude_process
from outfitter.dispatch.core.claude_roster import reconcile_claude_launch

_ARGV_HEADROOM_BYTES = 4096
_SHORT_ID = re.compile(r"^[0-9a-fA-F]{8}$")
_SUPPORTED_EFFORTS = frozenset({"low", "medium", "high", "max"})
_SUPPORTED_PERMISSION_MODES = frozenset(
    {"acceptEdits", "bypassPermissions", "default", "delegate", "dontAsk", "plan"}
)
_SUPPORTED_SETTING_SOURCES = frozenset({"user", "project", "local"})


def _nonempty(value: str | None, option: str) -> None:
    if value is not None and not value.strip():
        raise ClaudeLaunchValidationError(f"{option} must not be empty")
    if value is not None and "\x00" in value:
        raise ClaudeLaunchValidationError(f"{option} must not contain NUL")


def validate_claude_launch(envelope: ClaudeLaunchEnvelope) -> None:
    """Validate supported options and conflicts before any process invocation."""

    if envelope.provider != "claude":
        raise ClaudeLaunchValidationError("unsupported launch provider")
    if not envelope.cwd.is_absolute():
        raise ClaudeLaunchValidationError("cwd must be an absolute path")
    if not envelope.cwd.is_dir():
        raise ClaudeLaunchValidationError("cwd must be an existing directory")
    _nonempty(envelope.initial_text, "initial text")
    for value, option in (
        (envelope.display_name, "display name"),
        (envelope.agent, "agent"),
        (envelope.model, "model"),
        (envelope.effort, "effort"),
        (envelope.permission_mode, "permission mode"),
        (envelope.settings, "settings"),
        (envelope.mcp_config, "MCP config"),
    ):
        _nonempty(value, option)
    if envelope.effort is not None and envelope.effort not in _SUPPORTED_EFFORTS:
        raise ClaudeLaunchValidationError("unsupported Claude effort")
    if (
        envelope.permission_mode is not None
        and envelope.permission_mode not in _SUPPORTED_PERMISSION_MODES
    ):
        raise ClaudeLaunchValidationError("unsupported Claude permission mode")
    if envelope.setting_sources is not None:
        if not envelope.setting_sources:
            raise ClaudeLaunchValidationError("setting sources must not be empty")
        if any(source not in _SUPPORTED_SETTING_SOURCES for source in envelope.setting_sources):
            raise ClaudeLaunchValidationError("unsupported Claude setting source")
        if len(set(envelope.setting_sources)) != len(envelope.setting_sources):
            raise ClaudeLaunchValidationError("setting sources must be unique")
    if envelope.strict_mcp_config and envelope.mcp_config is None:
        raise ClaudeLaunchValidationError("strict MCP config requires an MCP config")
    if isinstance(envelope.worktree, str):
        _nonempty(envelope.worktree, "worktree name")
    for paths, option in (
        (envelope.plugin_dirs, "plugin directory"),
        (envelope.additional_dirs, "additional directory"),
    ):
        if len(set(paths)) != len(paths):
            raise ClaudeLaunchValidationError(f"{option} values must be unique")
        if any(not path.is_absolute() for path in paths):
            raise ClaudeLaunchValidationError(f"{option} must be absolute")
        if any(not path.is_dir() for path in paths):
            raise ClaudeLaunchValidationError(f"{option} must be an existing directory")


def project_claude_launch_argv(envelope: ClaudeLaunchEnvelope) -> tuple[str, ...]:
    """Project a validated envelope to exact direct-exec argv."""

    validate_claude_launch(envelope)
    argv = ["claude", "--bg"]
    options: Sequence[tuple[str, str | None]] = (
        ("--name", envelope.display_name),
        ("--agent", envelope.agent),
        ("--model", envelope.model),
        ("--effort", envelope.effort),
        ("--permission-mode", envelope.permission_mode),
        ("--settings", envelope.settings),
        (
            "--setting-sources",
            ",".join(envelope.setting_sources) if envelope.setting_sources is not None else None,
        ),
        ("--mcp-config", envelope.mcp_config),
    )
    for flag, value in options:
        if value is not None:
            argv.extend((flag, value))
    if envelope.strict_mcp_config:
        argv.append("--strict-mcp-config")
    for path in envelope.plugin_dirs:
        argv.extend(("--plugin-dir", str(path)))
    for path in envelope.additional_dirs:
        argv.extend(("--add-dir", str(path)))
    if envelope.worktree is True:
        argv.append("--worktree")
    elif isinstance(envelope.worktree, str):
        argv.extend(("--worktree", envelope.worktree))
    argv.extend(("--", envelope.initial_text))
    return tuple(argv)


def _encoded_size(values: Sequence[str]) -> int:
    return sum(len(value.encode()) + 1 for value in values)


def preflight_claude_launch(
    argv: tuple[str, ...], environment: Mapping[str, str], *, arg_max: int | None = None
) -> None:
    """Reject argv/environment payloads that cannot fit the platform exec boundary."""

    if any("\x00" in value for value in argv):
        raise ClaudeLaunchValidationError("Claude argv must not contain NUL")
    if any("=" in key or "\x00" in key or "\x00" in value for key, value in environment.items()):
        raise ClaudeLaunchValidationError("Claude environment is not executable")
    if arg_max is None:
        try:
            arg_max = os.sysconf("SC_ARG_MAX")
        except (AttributeError, OSError, ValueError):
            arg_max = 262_144
    environment_entries = [f"{key}={value}" for key, value in environment.items()]
    if _encoded_size(argv) + _encoded_size(environment_entries) + _ARGV_HEADROOM_BYTES > arg_max:
        raise ClaudeLaunchArgvLimitError("Claude launch exceeds the platform argv limit")


def _parse_short_id(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    matches = [token for line in lines for token in line.split() if _SHORT_ID.fullmatch(token)]
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        raise ClaudeLaunchOutputError("Claude background launch returned incompatible output")
    return unique[0].lower()


async def launch_claude_background(
    envelope: ClaudeLaunchEnvelope,
    *,
    run_process: ClaudeProcessRunner | None = None,
    environment: Mapping[str, str] | None = None,
    arg_max: int | None = None,
) -> ClaudeLaunchObservation:
    """Launch internally, then reconcile identity without registry mutation."""

    argv = project_claude_launch_argv(envelope)
    launch_environment = dict(os.environ if environment is None else environment)
    preflight_claude_launch(argv, launch_environment, arg_max=arg_max)
    runner = run_process or run_claude_process
    try:
        launched = await runner(argv, envelope.cwd, launch_environment)
    except OSError as exc:
        raise ClaudeLaunchCommandError("Claude CLI is unavailable") from exc
    except (ClaudeLaunchTimeoutError, ClaudeLaunchOutputLimitError) as exc:
        candidates = exc.short_id_candidates
        if len(candidates) != 1:
            raise ClaudeLaunchIndeterminateError(
                "Claude background launch is indeterminate; automatic retry is prohibited"
            ) from None
        short_id = candidates[0]
    except TimeoutError:
        raise ClaudeLaunchIndeterminateError(
            "Claude background launch is indeterminate; automatic retry is prohibited"
        ) from None
    else:
        if launched.returncode != 0:
            raise ClaudeLaunchCommandError("Claude background launch failed")
        short_id = _parse_short_id(launched.stdout)
    try:
        roster = await runner(
            ("claude", "agents", "--json", "--all"), envelope.cwd, launch_environment
        )
    except (
        FileNotFoundError,
        TimeoutError,
        ClaudeLaunchTimeoutError,
        ClaudeLaunchOutputLimitError,
    ):
        return ClaudeLaunchObservation(
            provider="claude",
            reconciliation="pending",
            short_id=short_id,
            provider_session_id=None,
            launch_cwd=str(envelope.cwd),
            pending_reason="roster_unavailable",
        )
    if roster.returncode != 0:
        return ClaudeLaunchObservation(
            provider="claude",
            reconciliation="pending",
            short_id=short_id,
            provider_session_id=None,
            launch_cwd=str(envelope.cwd),
            pending_reason="roster_unavailable",
        )
    return reconcile_claude_launch(
        short_id=short_id, launch_cwd=envelope.cwd, roster_output=roster.stdout
    )


__all__ = [
    "ClaudeLaunchAmbiguousError",
    "ClaudeLaunchArgvLimitError",
    "ClaudeLaunchCommandError",
    "ClaudeLaunchEnvelope",
    "ClaudeLaunchError",
    "ClaudeLaunchIndeterminateError",
    "ClaudeLaunchObservation",
    "ClaudeLaunchOutputError",
    "ClaudeLaunchOutputLimitError",
    "ClaudeLaunchTimeoutError",
    "ClaudeLaunchValidationError",
    "ClaudeProcessResult",
    "launch_claude_background",
    "preflight_claude_launch",
    "project_claude_launch_argv",
    "reconcile_claude_launch",
    "run_claude_process",
    "validate_claude_launch",
]
