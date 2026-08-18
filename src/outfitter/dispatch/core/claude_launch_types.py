"""Shared internal types for Claude background-session launch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal


class ClaudeLaunchError(Exception):
    """Base for internal launch failures with content-free messages."""


class ClaudeLaunchValidationError(ClaudeLaunchError):
    """The canonical launch envelope is unsupported or conflicting."""


class ClaudeLaunchArgvLimitError(ClaudeLaunchError):
    """The encoded argv and environment would exceed the platform limit."""


class ClaudeLaunchTimeoutError(ClaudeLaunchError):
    """A bounded Claude CLI invocation timed out."""

    def __init__(self, message: str, *, short_id_candidates: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.short_id_candidates = short_id_candidates


class ClaudeLaunchOutputLimitError(ClaudeLaunchError):
    """A Claude CLI stream exceeded its bounded read limit."""

    def __init__(self, message: str, *, short_id_candidates: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.short_id_candidates = short_id_candidates


class ClaudeLaunchCommandError(ClaudeLaunchError):
    """The Claude CLI rejected a launch or roster query."""


class ClaudeLaunchOutputError(ClaudeLaunchError):
    """The Claude CLI returned incompatible launch or roster output."""


class ClaudeLaunchAmbiguousError(ClaudeLaunchError):
    """The provisional launch matched more than one roster row."""


class ClaudeLaunchIndeterminateError(ClaudeLaunchError):
    """A launch may have created a session, so automatic retry is prohibited."""

    retry_safe: ClassVar[Literal[False]] = False


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
    pending_reason: Literal["roster_absent", "identity_pending", "roster_unavailable"] | None = None
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
