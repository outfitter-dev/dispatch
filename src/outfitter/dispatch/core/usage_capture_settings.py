"""Claude Code settings access for the usage-capture lifecycle.

Reads are bounded and fail explicit (a malformed file is reported, never
silently treated as empty). Writes are atomic and preserve every settings key
other than ``statusLine`` plus the file's existing permissions; formatting is
whatever a two-space-indented JSON round-trip produces.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from outfitter.dispatch.core.usage_capture import write_private_file

STATUSLINE_KEY = "statusLine"
DISABLE_ALL_HOOKS_KEY = "disableAllHooks"
_MAX_SETTINGS_BYTES = 1024 * 1024


def expanded_command_path(token: str) -> Path:
    """``token`` as a path, with environment variables and ``~`` expanded.

    Claude executes ``statusLine.command`` through a shell, so spellings such
    as ``$HOME/.dispatch/claude/statusline.sh``, ``${HOME}/...``,
    ``$DISPATCH_HOME/claude/statusline.sh``, or ``~/...`` all reach the same
    file as the absolute path install writes. Expansion resolves against the
    *current process* environment — Claude's shell environment may differ, but
    the variables relevant to recognizing the managed wrapper (``HOME``,
    ``DISPATCH_HOME``) are the stable ones both sides share. A variable that
    is unset stays verbatim and simply fails to match.
    """
    return Path(os.path.expandvars(token)).expanduser()


def command_token_names_path(token: str, target: Path) -> bool:
    """Whether shell-command token ``token`` names the same file as ``target``.

    Comparison-only normalization for *recognizing* the managed wrapper —
    never for deciding what gets written (settings writes keep their
    symlink-preserving spelling). After variable/tilde expansion
    (``expanded_command_path``), both sides are resolved with
    ``Path.resolve(strict=False)`` so aliases reach the same identity:
    a ``statusLine.command`` may spell the wrapper through a symlink or a
    ``..``-containing path, and the wrapper path itself may sit under a
    symlinked segment (a symlinked home dir, macOS ``/tmp`` → ``/private``).
    ``strict=False`` keeps nonexistent paths normalizing lexically instead of
    raising, and a dangling symlink still resolves through its link text —
    which matters when install runs before the wrapper file exists.
    """
    expanded = expanded_command_path(token)
    if expanded == target:
        return True
    return expanded.resolve(strict=False) == target.resolve(strict=False)


def claude_settings_path() -> Path:
    """Claude Code user settings file (``CLAUDE_CONFIG_DIR`` overrides ``~/.claude``)."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
    return base / "settings.json"


@dataclass(frozen=True)
class LoadedSettings:
    """One parsed settings file.

    ``malformed`` means the content was read but is unusable (bad JSON,
    non-object root, oversize). ``unreadable`` means the content could not be
    read at all (permissions, I/O error) — a critically different fact: an
    unreadable file may still point at the Dispatch wrapper, so lifecycle
    cleanup must not treat it like provably-not-pointing malformed content.
    """

    path: Path
    data: dict[str, JsonValue] | None
    malformed: bool
    unreadable: bool = False


def load_settings(path: Path) -> LoadedSettings:
    """Parse one settings file; missing reads as absent, unparseable as malformed.

    An ``OSError`` other than the file being absent (``PermissionError``, an
    I/O failure mid-read) reads as ``unreadable``, never as malformed: the
    content exists but cannot be inspected.
    """
    try:
        with path.open("rb") as handle:
            payload = handle.read(_MAX_SETTINGS_BYTES + 1)
    except FileNotFoundError:
        return LoadedSettings(path=path, data=None, malformed=False)
    except OSError:
        return LoadedSettings(path=path, data=None, malformed=False, unreadable=True)
    if len(payload) > _MAX_SETTINGS_BYTES:
        return LoadedSettings(path=path, data=None, malformed=True)
    try:
        parsed: object = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        return LoadedSettings(path=path, data=None, malformed=True)
    if not isinstance(parsed, dict):
        return LoadedSettings(path=path, data=None, malformed=True)
    return LoadedSettings(path=path, data=cast(dict[str, JsonValue], parsed), malformed=False)


def write_claude_settings(path: Path, settings: dict[str, JsonValue]) -> None:
    """Atomically replace the settings file, keeping its existing permissions.

    The path is resolved first so a symlinked settings file (dotfiles-managed
    setups) has its *target* rewritten and the symlink itself survives the
    temp-file + ``os.replace`` dance; a dangling symlink writes the file its
    target names, which also makes the link valid.
    """
    target = path.resolve()
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = 0o600
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(settings, indent=2, ensure_ascii=False).encode() + b"\n"
    write_private_file(target, encoded, mode=mode)


@dataclass(frozen=True)
class ClaudeStatuslineEnvironment:
    """Effective statusline configuration around the Claude user settings file.

    ``override_paths`` lists higher-precedence project settings files that
    declare their own ``statusLine`` and therefore shadow the user setting.
    """

    settings_path: Path
    settings: dict[str, JsonValue] | None
    settings_malformed: bool
    settings_unreadable: bool
    disable_all_hooks: bool
    override_paths: tuple[Path, ...]

    @property
    def statusline(self) -> dict[str, JsonValue] | None:
        """The user-level ``statusLine`` object, when one is configured."""
        if self.settings is None:
            return None
        value = self.settings.get(STATUSLINE_KEY)
        return value if isinstance(value, dict) else None

    @property
    def statusline_unsupported(self) -> bool:
        """A ``statusLine`` key is present but is not a JSON object.

        Hand-edited or stale values (a string, a list, ``null``) cannot be
        preserved in the restoration record's object shape, so they must never
        be treated as "absent": install would record ``had_statusline=False``
        and overwrite the value, and remove would then delete the key —
        irreversible loss. Callers block install and refuse default remove on
        this state instead.
        """
        if self.settings is None or STATUSLINE_KEY not in self.settings:
            return False
        return not isinstance(self.settings.get(STATUSLINE_KEY), dict)

    def statusline_command(self) -> str | None:
        block = self.statusline
        if block is None:
            return None
        command = block.get("command")
        return command if isinstance(command, str) else None

    def points_at(self, wrapper: Path) -> bool:
        """Whether the effective user statusline command is ``wrapper``.

        Matches the bare path (pre-quoting installs wrote it verbatim), the
        shell-quoted form install writes now that the command is quoted for
        Claude's shell, and hand-edited spellings that reach the same file:
        variable/tilde forms (``~``, ``$HOME``, ``${HOME}``,
        ``$DISPATCH_HOME``) plus symlink and ``..`` aliases — see
        ``command_token_names_path``. Treating those as foreign would misread
        an installed state as drifted, or let install record the wrapper
        spelling as the "original renderer".
        """
        command = self.statusline_command()
        if command is None:
            return False
        if command == str(wrapper) or command_token_names_path(command, wrapper):
            return True
        try:
            parts = shlex.split(command)
        except ValueError:
            return False
        return len(parts) == 1 and command_token_names_path(parts[0], wrapper)


def inspect_claude_environment(
    *,
    settings_path: Path | None = None,
    project_dir: Path | None = None,
) -> ClaudeStatuslineEnvironment:
    """Read the user settings plus higher-precedence project/local settings."""
    path = settings_path if settings_path is not None else claude_settings_path()
    user = load_settings(path)
    disable = _disable_all_hooks(user.data)
    overrides: list[Path] = []
    project = project_dir if project_dir is not None else Path.cwd()
    for candidate in (
        project / ".claude" / "settings.local.json",
        project / ".claude" / "settings.json",
    ):
        loaded = load_settings(candidate)
        if loaded.data is None:
            continue
        if STATUSLINE_KEY in loaded.data:
            overrides.append(candidate)
        disable = disable or _disable_all_hooks(loaded.data)
    return ClaudeStatuslineEnvironment(
        settings_path=path,
        settings=user.data,
        settings_malformed=user.malformed,
        settings_unreadable=user.unreadable,
        disable_all_hooks=disable,
        override_paths=tuple(overrides),
    )


def _disable_all_hooks(data: dict[str, JsonValue] | None) -> bool:
    return data is not None and data.get(DISABLE_ALL_HOOKS_KEY) is True
