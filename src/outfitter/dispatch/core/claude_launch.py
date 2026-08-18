"""Internal Claude background-session launch and identity reconciliation."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

_MAX_OUTPUT_BYTES = 1024 * 1024
_COMMAND_TIMEOUT_SECONDS = 10.0
_ARGV_HEADROOM_BYTES = 4096
_SHORT_ID = re.compile(r"^[0-9a-fA-F]{8}$")
_SUPPORTED_EFFORTS = frozenset({"low", "medium", "high", "max"})
_SUPPORTED_PERMISSION_MODES = frozenset(
    {"acceptEdits", "bypassPermissions", "default", "delegate", "dontAsk", "plan"}
)
_SUPPORTED_SETTING_SOURCES = frozenset({"user", "project", "local"})


class ClaudeLaunchError(Exception):
    """Base for internal launch failures with content-free messages."""


class ClaudeLaunchValidationError(ClaudeLaunchError):
    """The canonical launch envelope is unsupported or conflicting."""


class ClaudeLaunchArgvLimitError(ClaudeLaunchError):
    """The encoded argv and environment would exceed the platform limit."""


class ClaudeLaunchTimeoutError(ClaudeLaunchError):
    """A bounded Claude CLI invocation timed out."""


class ClaudeLaunchOutputLimitError(ClaudeLaunchError):
    """A Claude CLI stream exceeded its bounded read limit."""


class ClaudeLaunchCommandError(ClaudeLaunchError):
    """The Claude CLI rejected a launch or roster query."""


class ClaudeLaunchOutputError(ClaudeLaunchError):
    """The Claude CLI returned incompatible launch or roster output."""


class ClaudeLaunchAmbiguousError(ClaudeLaunchError):
    """The provisional launch matched more than one roster row."""


@dataclass(frozen=True)
class ClaudeLaunchEnvelope:
    """Canonical immutable input for the disabled-by-default Claude launcher."""

    cwd: Path
    initial_text: str = field(repr=False)
    provider: Literal["claude"] = "claude"
    display_name: str | None = None
    agent: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    settings: str | None = None
    setting_sources: tuple[str, ...] | None = None
    mcp_config: str | None = None
    strict_mcp_config: bool = False
    plugin_dirs: tuple[Path, ...] = ()
    additional_dirs: tuple[Path, ...] = ()
    worktree: bool | str = False


@dataclass(frozen=True)
class ClaudeLaunchObservation:
    """Content-free launch metadata safe to retain as a provider observation."""

    provider: Literal["claude"]
    reconciliation: Literal["reconciled", "pending"]
    short_id: str
    provider_session_id: str | None
    launch_cwd: str
    observed_cwd: str | None = None
    observed_name: str | None = None
    observed_kind: str | None = None
    observed_state: str | None = None
    observed_worktree: str | None = None


@dataclass(frozen=True)
class ClaudeProcessResult:
    returncode: int
    stdout: str
    stderr: str = ""


ClaudeProcessRunner = Callable[
    [tuple[str, ...], Path, Mapping[str, str]], Awaitable[ClaudeProcessResult]
]


async def _read_bounded(stream: asyncio.StreamReader) -> bytes:
    data = bytearray()
    while chunk := await stream.read(64 * 1024):
        data.extend(chunk)
        if len(data) > _MAX_OUTPUT_BYTES:
            raise ClaudeLaunchOutputLimitError("Claude CLI output exceeded the safe limit")
    return bytes(data)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await process.wait()


async def run_claude_process(
    argv: tuple[str, ...], cwd: Path, environment: Mapping[str, str]
) -> ClaudeProcessResult:
    """Run one Claude CLI command without a shell under fixed resource bounds."""

    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(environment),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=64 * 1024,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    tasks = [
        asyncio.create_task(_read_bounded(process.stdout)),
        asyncio.create_task(_read_bounded(process.stderr)),
        asyncio.create_task(process.wait()),
    ]
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=_COMMAND_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        for task in tasks:
            task.cancel()
        await asyncio.shield(_terminate(process))
        await asyncio.gather(*tasks, return_exceptions=True)
        raise ClaudeLaunchTimeoutError("Claude CLI command timed out") from exc
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.shield(_terminate(process))
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    stdout = cast(bytes, results[0]).decode(errors="replace")
    stderr = cast(bytes, results[1]).decode(errors="replace")
    return ClaudeProcessResult(process.returncode or 0, stdout, stderr)


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
    if not envelope.initial_text:
        raise ClaudeLaunchValidationError("initial text must not be empty")
    if "\x00" in envelope.initial_text:
        raise ClaudeLaunchValidationError("initial text must not contain NUL")
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
    argv.append(envelope.initial_text)
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


def _optional_text(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    return value if isinstance(value, str) and value else None


def reconcile_claude_launch(
    *, short_id: str, launch_cwd: Path, roster_output: str
) -> ClaudeLaunchObservation:
    """Resolve one provisional short ID to exactly one authoritative provider UUID."""

    try:
        raw = json.loads(roster_output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClaudeLaunchOutputError("Claude agent roster returned invalid JSON") from exc
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise ClaudeLaunchOutputError("Claude agent roster returned incompatible JSON")
    rows = [
        cast(dict[str, object], row)
        for row in raw
        if isinstance(row.get("id"), str) and str(row["id"]).lower() == short_id.lower()
    ]
    if not rows:
        return ClaudeLaunchObservation(
            provider="claude",
            reconciliation="pending",
            short_id=short_id,
            provider_session_id=None,
            launch_cwd=str(launch_cwd),
        )
    if len(rows) > 1:
        raise ClaudeLaunchAmbiguousError("Claude launch matched multiple global roster rows")
    row = rows[0]
    kind = row.get("kind")
    if kind is not None and kind != "background":
        raise ClaudeLaunchOutputError("Claude roster row is not a background session")
    session_id = row.get("sessionId")
    if session_id is None:
        return ClaudeLaunchObservation(
            provider="claude",
            reconciliation="pending",
            short_id=short_id,
            provider_session_id=None,
            launch_cwd=str(launch_cwd),
            observed_cwd=_optional_text(row, "cwd"),
            observed_name=_optional_text(row, "name"),
            observed_kind=_optional_text(row, "kind"),
            observed_state=_optional_text(row, "state"),
            observed_worktree=_optional_text(row, "worktree"),
        )
    if not isinstance(session_id, str):
        raise ClaudeLaunchOutputError("Claude roster session identity has an incompatible type")
    try:
        provider_session_id = str(uuid.UUID(session_id))
    except ValueError as exc:
        raise ClaudeLaunchOutputError("Claude roster session identity is not a full UUID") from exc
    return ClaudeLaunchObservation(
        provider="claude",
        reconciliation="reconciled",
        short_id=short_id,
        provider_session_id=provider_session_id,
        launch_cwd=str(launch_cwd),
        observed_cwd=_optional_text(row, "cwd"),
        observed_name=_optional_text(row, "name"),
        observed_kind=_optional_text(row, "kind"),
        observed_state=_optional_text(row, "state"),
        observed_worktree=_optional_text(row, "worktree"),
    )


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
    except FileNotFoundError as exc:
        raise ClaudeLaunchCommandError("Claude CLI is unavailable") from exc
    except TimeoutError as exc:
        raise ClaudeLaunchTimeoutError("Claude CLI command timed out") from exc
    if launched.returncode != 0:
        raise ClaudeLaunchCommandError("Claude background launch failed")
    short_id = _parse_short_id(launched.stdout)
    try:
        roster = await runner(
            ("claude", "agents", "--json", "--all"), envelope.cwd, launch_environment
        )
    except FileNotFoundError as exc:
        raise ClaudeLaunchCommandError("Claude CLI is unavailable") from exc
    except TimeoutError as exc:
        raise ClaudeLaunchTimeoutError("Claude CLI command timed out") from exc
    if roster.returncode != 0:
        raise ClaudeLaunchCommandError("Claude global roster query failed")
    return reconcile_claude_launch(
        short_id=short_id, launch_cwd=envelope.cwd, roster_output=roster.stdout
    )
