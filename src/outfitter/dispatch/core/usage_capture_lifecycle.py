"""Install/status/remove lifecycle for Claude statusline usage capture.

Install replaces only ``statusLine.command`` in Claude settings with the
Dispatch wrapper; every other key of the original object stays live and the
complete original object is preserved verbatim in the restoration record.
Writes happen in crash-safe order — record, wrapper, then Claude settings —
so a failure between steps is recoverable by re-running install or remove.
Remove restores settings first, then deletes Dispatch artifacts.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, JsonValue

from outfitter.dispatch.core.claude_statusline import read_claude_statusline_snapshot
from outfitter.dispatch.core.usage_capture import (
    UsageCaptureRecord,
    ensure_private_dir,
    read_usage_capture_record,
    record_too_large,
    usage_capture_record_path,
    usage_capture_wrapper_path,
    write_private_file,
    write_usage_capture_record,
)
from outfitter.dispatch.core.usage_capture_settings import (
    STATUSLINE_KEY,
    ClaudeStatuslineEnvironment,
    command_token_names_path,
    expanded_command_path,
    load_settings,
    write_claude_settings,
)

_RUN_ARGS = "usage-capture run --provider claude"
RUN_COMMAND = f"dispatch {_RUN_ARGS}"
CAPTURE_FRESH_SECONDS = 600
_DEFAULT_DISPATCH_HOME = "~/.dispatch"
# Conservative substring markers for commands that already invoke a Dispatch
# capture entry point (recording one as the "original renderer" would make the
# wrapper delegate back into Dispatch: recursion for `run`, double capture and
# a bogus restore target for the deprecated helper).
_CAPTURE_COMMAND_MARKERS = ("dispatch usage-capture run", "dispatch-claude-statusline")


class SettingsChangedError(RuntimeError):
    """Claude settings changed between plan and apply; nothing was written."""


class ArtifactsChangedError(RuntimeError):
    """Dispatch artifacts changed between plan and apply; settings untouched."""


def _dispatch_home_override() -> str | None:
    """The non-default ``DISPATCH_HOME`` install ran under, if any.

    A relative override is anchored to the install cwd before it is baked
    anywhere: the wrapper runs from whatever cwd Claude Code happens to use,
    so persisting the relative form would silently retarget the record and
    snapshots. ``~`` is expanded for the same reason (the wrapper quotes the
    value, so the shell would not). Already-absolute values pass through
    untouched so existing wrappers keep matching byte-for-byte.
    """
    value = os.environ.get("DISPATCH_HOME")
    if value is None:
        return None
    expanded = Path(value).expanduser()
    if expanded == Path(_DEFAULT_DISPATCH_HOME).expanduser():
        return None
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return str(expanded)


def _normalized_home(value: str) -> Path:
    """Normalize a baked or effective ``DISPATCH_HOME`` value for comparison."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _resolve_dispatch_executable() -> Path | None:
    """Durable absolute path of the ``dispatch`` executable, if resolvable.

    The wrapper runs under Claude Code's environment, not the one install ran
    in — a bare ``dispatch`` resolves against *Claude's* PATH, which under the
    documented ``uv run dispatch usage-capture install`` path no longer holds
    the transient venv shim that made the install-time preflight pass. Prefer
    the entry point actually running this process (``sys.argv[0]`` when its
    basename is ``dispatch``) so the baked path belongs to the environment that
    performed the install, then fall back to a resolved PATH lookup. ``None``
    means neither yields an existing executable; the wrapper then falls back
    to a bare ``dispatch`` (and install warns about PATH).
    """
    candidates: list[Path] = []
    argv0 = Path(sys.argv[0])
    if argv0.name == "dispatch":
        candidates.append(argv0)
    which = shutil.which("dispatch")
    if which is not None:
        candidates.append(Path(which))
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    return None


def _executable_looks_ephemeral(executable: Path) -> bool:
    """Whether a resolved executable lives somewhere a cleanup may delete."""
    if ".cache" in executable.parts or "Caches" in executable.parts:
        return True
    roots = {Path("/tmp"), Path("/var/tmp"), Path(tempfile.gettempdir())}
    with suppress(OSError):
        roots.add(Path(tempfile.gettempdir()).resolve())
    return any(executable.is_relative_to(root) for root in roots)


def _run_command() -> str:
    """The capture command the wrapper and record carry.

    Bakes the absolute executable resolved at install time when one exists;
    only when nothing resolves does it fall back to the legacy bare
    ``dispatch`` spelling (PATH resolution at statusline time).
    """
    executable = _resolve_dispatch_executable()
    if executable is None:
        return RUN_COMMAND
    return f"{shlex.quote(str(executable))} {_RUN_ARGS}"


def wrapper_content() -> str:
    """Wrapper script body for the effective ``DISPATCH_HOME``.

    A non-default home is baked into the script: Claude Code invokes the
    wrapper with its own environment, so without this the run path would read
    the record and write snapshots under the default home instead of the one
    install used. The dispatch executable is baked as an absolute path for the
    same reason (Claude's PATH need not resolve ``dispatch``). ``run`` never
    reads Claude settings, so ``CLAUDE_CONFIG_DIR`` is deliberately not baked
    in.
    """
    override = _dispatch_home_override()
    command = _run_command()
    if override is None:
        return f"#!/bin/sh\nexec {command}\n"
    return (
        f"#!/bin/sh\nDISPATCH_HOME={shlex.quote(override)}\nexport DISPATCH_HOME\nexec {command}\n"
    )


def installed_command() -> str:
    """The command the wrapper executes, as recorded in the restoration record."""
    override = _dispatch_home_override()
    command = _run_command()
    if override is None:
        return command
    return f"DISPATCH_HOME={shlex.quote(override)} {command}"


def _parse_dispatch_wrapper(content: str) -> str | None:
    """The executable token of a structurally-valid Dispatch capture wrapper.

    Returns the ``dispatch`` executable spelling the wrapper execs (absolute
    path or bare ``dispatch``) when ``content`` is a Dispatch capture wrapper
    for the effective home, ``None`` otherwise. Byte-exactness is deliberately
    not required here: the wrapper bakes the dispatch executable resolved at
    install time, and status may run under a different environment (or inspect
    a pre-baking install that used a bare ``dispatch``). Any wrapper that
    execs a dispatch capture entry point with the effective ``DISPATCH_HOME``
    is structurally current — legacy content must never flip to
    broken/drifted for spelling alone — while reinstall still refreshes stale
    spellings silently because the install plan compares exact bytes
    (``wrapper_exact``).
    """
    lines = content.splitlines()
    override = _dispatch_home_override()
    if override is None:
        if len(lines) != 2 or lines[0] != "#!/bin/sh":
            return None
        exec_line = lines[1]
    else:
        if len(lines) != 4 or lines[0] != "#!/bin/sh" or lines[2] != "export DISPATCH_HOME":
            return None
        if not lines[1].startswith("DISPATCH_HOME="):
            return None
        try:
            baked = shlex.split(lines[1][len("DISPATCH_HOME=") :])
        except ValueError:
            return None
        if len(baked) != 1 or _normalized_home(baked[0]) != Path(override):
            return None
        exec_line = lines[3]
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return None
    if (
        len(tokens) == 6
        and tokens[0] == "exec"
        and Path(tokens[1]).name == "dispatch"
        and tokens[2:] == ["usage-capture", "run", "--provider", "claude"]
    ):
        return tokens[1]
    return None


def _missing_baked_executable(executable_token: str) -> Path | None:
    """The baked absolute executable that no longer runs, if that is the case.

    A wrapper whose baked absolute executable was deleted or moved (venv
    rebuilt, cache cleaned) fails at ``exec`` on every statusline refresh, so
    it must read broken rather than installed. A bare ``dispatch`` token keeps
    relying on PATH resolution at statusline time (the PATH warning covers
    that case), so it is never reported missing here.
    """
    baked = Path(executable_token)
    if not baked.is_absolute():
        return None
    if baked.is_file() and os.access(baked, os.X_OK):
        return None
    return baked


def _invokes_capture_entry_point(command: str | None, wrapper: Path) -> bool:
    """Whether ``command`` invokes a Dispatch capture entry point.

    Two conservative layers, both erring toward blocking install with a clear
    message rather than guessing that an embedded invocation is safe: the raw
    substring markers as a backstop (they also cover commands shlex refuses to
    parse), plus a shell-token pass so quoted spellings such as
    ``'/usr/local/bin/dispatch' usage-capture run`` cannot be recorded as the
    "original renderer". The substring backstop deliberately compares literal
    text — expanding variables inside arbitrary raw strings could over-match —
    while the token pass expands ``~`` and environment variables per token, so
    ``sh $DISPATCH_HOME/claude/statusline.sh`` is still caught. An invocation
    hidden behind opaque indirection — a user script that calls dispatch
    internally, ``eval`` on a built-up string — stays undetectable: parsing
    arbitrary shell is out of scope, and such a command gives install no
    textual evidence to inspect. A renamed *copy* of the deprecated helper
    invoked bare is in the same class (no arguments, no recognizable name,
    no filesystem link back to Dispatch); a renamed copy or symlink of the
    ``dispatch`` binary is caught whenever the capture arguments appear
    textually, because the token pass blocks on the ``usage-capture run``
    argument shape regardless of the invoked binary's identity.
    """
    if command is None:
        return False
    if any(marker in command for marker in (*_CAPTURE_COMMAND_MARKERS, str(wrapper))):
        return True
    return _tokens_invoke_capture_entry_point(command, str(wrapper))


def _tokens_invoke_capture_entry_point(command: str, wrapper: str) -> bool:
    """Shell-token scan for capture entry points behind quoting.

    Matches the wrapper path as a whole token — including ``~``,
    environment-variable, symlink, and ``..`` spellings that reach it
    (``command_token_names_path``, the same comparison-only normalization
    ``points_at`` applies) — any token that names the deprecated helper by
    basename or resolves to it through a symlink, and any token directly
    followed by ``usage-capture run``. That last rule deliberately ignores
    the invoked binary's identity: ``usage-capture run`` is Dispatch's own
    capture grammar, so a token in front of it is either the dispatch
    executable under a spelling basename checks cannot see (a symlink such
    as ``dx``, a renamed copy of the binary) or something masquerading as
    it — and recording either as the "original renderer" makes the wrapper
    recurse on every statusline refresh. Blocking install is cheap and
    recoverable (the message names the fix); runaway recursion is not, so
    this fails closed like the substring backstop. A token that still
    contains whitespace (for example the payload of ``sh -c "..."``) is
    re-split recursively; every pass strips a quoting level, so the
    recursion terminates.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    wrapper_path = Path(wrapper)
    for index, token in enumerate(tokens):
        if token == wrapper or command_token_names_path(token, wrapper_path):
            return True
        if _token_names_deprecated_helper(token):
            return True
        if tokens[index + 1 : index + 3] == ["usage-capture", "run"]:
            return True
        if any(ch.isspace() for ch in token) and _tokens_invoke_capture_entry_point(token, wrapper):
            return True
    return False


def _token_names_deprecated_helper(token: str) -> bool:
    """Whether ``token`` names the deprecated capture helper, symlinks included.

    Basename first (bare ``dispatch-claude-statusline`` on PATH, absolute or
    variable spellings), then the resolved target's basename via the same
    comparison-only ``Path.resolve(strict=False)`` normalization
    ``command_token_names_path`` uses, so a symlink alias such as
    ``~/bin/old-status`` -> ``.../dispatch-claude-statusline`` is still
    caught. A bare alias resolvable only through PATH lookup stays
    undetectable — expanding arbitrary tokens against PATH would over-reach —
    and falls in the documented opaque-indirection class.
    """
    expanded = expanded_command_path(token)
    if expanded.name == "dispatch-claude-statusline":
        return True
    return expanded.resolve(strict=False).name == "dispatch-claude-statusline"


LifecycleState = Literal["not_installed", "prepared", "installed", "drifted", "broken", "disabled"]


class UsageCaptureStatus(BaseModel):
    """Bounded lifecycle status. Never carries the original command string."""

    provider: Literal["claude"] = "claude"
    state: LifecycleState
    settings_path: str
    settings_malformed: bool
    settings_unreadable: bool
    settings_statusline_unsupported: bool
    settings_points_at_wrapper: bool
    disable_all_hooks: bool
    override_paths: list[str]
    wrapper_path: str
    wrapper_exists: bool
    wrapper_executable: bool
    wrapper_current: bool
    wrapper_missing_executable: str | None
    record_exists: bool
    record_valid: bool
    original_renderer_recorded: bool | None
    dispatch_on_path: bool
    last_capture_at: str | None
    capture_fresh: bool | None


@dataclass(frozen=True)
class _Artifacts:
    wrapper_path: Path
    record_path: Path
    wrapper_exists: bool
    wrapper_executable: bool
    wrapper_current: bool
    wrapper_exact: bool
    wrapper_missing_executable: Path | None
    record_exists: bool
    record: UsageCaptureRecord | None


def _inspect_artifacts(directory: Path | None) -> _Artifacts:
    wrapper = usage_capture_wrapper_path(directory)
    record_path = usage_capture_record_path(directory)
    exists = wrapper.is_file()
    executable = exists and os.access(wrapper, os.X_OK)
    # `exact` (byte-identical to what install would write today) drives the
    # reinstall refresh; `current` (structurally a Dispatch wrapper for the
    # effective home whose baked absolute executable still runs) drives state.
    # An install whose baked executable was resolved by a different
    # environment — or a legacy bare-`dispatch` wrapper — never flips to
    # broken/drifted for its spelling, but a baked absolute executable that
    # was later deleted or moved fails every `exec`, so it must.
    current = False
    exact = False
    missing_executable: Path | None = None
    if exists:
        with suppress(OSError):
            raw = wrapper.read_bytes()
            exact = raw == wrapper_content().encode()
            with suppress(UnicodeDecodeError):
                token = _parse_dispatch_wrapper(raw.decode())
                if token is not None:
                    missing_executable = _missing_baked_executable(token)
                    current = missing_executable is None
    return _Artifacts(
        wrapper_path=wrapper,
        record_path=record_path,
        wrapper_exists=exists,
        wrapper_executable=executable,
        wrapper_current=current and executable,
        wrapper_exact=exact and executable,
        wrapper_missing_executable=missing_executable,
        record_exists=record_path.exists(),
        record=read_usage_capture_record(path=record_path),
    )


def _settings_match_original(
    env: ClaudeStatuslineEnvironment, record: UsageCaptureRecord | None
) -> bool:
    """Whether live settings still hold exactly what the record would restore."""
    if record is None:
        return False
    if env.statusline_unsupported:
        # A present-but-non-object statusLine is neither the recorded original
        # nor "absent": treat it as drift so remove refuses to touch it.
        return False
    if record.had_statusline:
        return env.statusline == record.original_statusline
    return env.statusline is None


def _state(env: ClaudeStatuslineEnvironment, artifacts: _Artifacts) -> LifecycleState:
    has_artifacts = artifacts.wrapper_exists or artifacts.record_exists
    if env.settings_unreadable or env.settings_malformed:
        return "broken" if has_artifacts else "not_installed"
    if env.points_at(artifacts.wrapper_path):
        if not artifacts.wrapper_current or artifacts.record is None:
            return "broken"
        if env.disable_all_hooks or env.override_paths:
            return "disabled"
        return "installed"
    if not has_artifacts:
        return "not_installed"
    if _settings_match_original(env, artifacts.record):
        return "prepared"
    return "drifted"


def environment_warnings(
    env: ClaudeStatuslineEnvironment, *, dispatch_on_path: bool | None = None
) -> tuple[str, ...]:
    """Actionable environment findings that do not block by themselves."""
    warnings: list[str] = []
    if env.disable_all_hooks:
        warnings.append(
            "disableAllHooks is enabled in Claude settings; Claude may suppress the statusline."
        )
    warnings.extend(
        f"higher-precedence statusLine in {path} overrides the user setting"
        for path in env.override_paths
    )
    executable = _resolve_dispatch_executable()
    if executable is None:
        # PATH only matters for the bare-`dispatch` fallback: a resolved
        # absolute executable is baked into the wrapper, which then runs
        # regardless of Claude's PATH.
        on_path = (
            shutil.which("dispatch") is not None if dispatch_on_path is None else dispatch_on_path
        )
        if not on_path:
            warnings.append(
                "`dispatch` is not resolvable on PATH; the wrapper will fail until it is."
            )
    elif _executable_looks_ephemeral(executable):
        warnings.append(
            f"dispatch resolves to {executable}, which looks like a cache or temp "
            "location; the path baked into the wrapper may break when that "
            "environment is cleaned up. Prefer a durable install "
            "(for example `uv tool install`)."
        )
    return tuple(warnings)


def usage_capture_status(
    env: ClaudeStatuslineEnvironment,
    *,
    directory: Path | None = None,
    snapshot_path: Path | None = None,
    now: datetime | None = None,
    dispatch_on_path: bool | None = None,
) -> UsageCaptureStatus:
    """Compute the bounded lifecycle state plus its supporting facts."""
    artifacts = _inspect_artifacts(directory)
    snapshot = read_claude_statusline_snapshot(path=snapshot_path)
    capture_fresh: bool | None = None
    if snapshot is not None:
        moment = now if now is not None else datetime.now(UTC)
        age = (moment - datetime.fromisoformat(snapshot.observed_at)).total_seconds()
        capture_fresh = age <= CAPTURE_FRESH_SECONDS
    on_path = shutil.which("dispatch") is not None if dispatch_on_path is None else dispatch_on_path
    return UsageCaptureStatus(
        state=_state(env, artifacts),
        settings_path=str(env.settings_path),
        settings_malformed=env.settings_malformed,
        settings_unreadable=env.settings_unreadable,
        settings_statusline_unsupported=env.statusline_unsupported,
        settings_points_at_wrapper=env.points_at(artifacts.wrapper_path),
        disable_all_hooks=env.disable_all_hooks,
        override_paths=[str(path) for path in env.override_paths],
        wrapper_path=str(artifacts.wrapper_path),
        wrapper_exists=artifacts.wrapper_exists,
        wrapper_executable=artifacts.wrapper_executable,
        wrapper_current=artifacts.wrapper_current,
        wrapper_missing_executable=(
            str(artifacts.wrapper_missing_executable)
            if artifacts.wrapper_missing_executable is not None
            else None
        ),
        record_exists=artifacts.record_exists,
        record_valid=artifacts.record is not None,
        original_renderer_recorded=(
            artifacts.record.had_statusline if artifacts.record is not None else None
        ),
        dispatch_on_path=on_path,
        last_capture_at=snapshot.observed_at if snapshot is not None else None,
        capture_fresh=capture_fresh,
    )


@dataclass(frozen=True)
class InstallPlan:
    state: LifecycleState
    wrapper_path: Path
    record_path: Path
    settings_path: Path
    write_record: bool
    write_wrapper: bool
    write_settings: bool
    blocked: str | None
    warnings: tuple[str, ...]

    @property
    def changes_anything(self) -> bool:
        return self.write_record or self.write_wrapper or self.write_settings


def plan_install(
    env: ClaudeStatuslineEnvironment,
    *,
    directory: Path | None = None,
    dispatch_on_path: bool | None = None,
) -> InstallPlan:
    """Decide what install must write; refuses drifted settings like remove does."""
    artifacts = _inspect_artifacts(directory)
    state = _state(env, artifacts)
    warnings = environment_warnings(env, dispatch_on_path=dispatch_on_path)
    if env.settings_unreadable:
        # Unreadable is not malformed: the content exists but cannot be
        # inspected, so install can neither record the original statusline nor
        # rebuild the file without destroying whatever is in it.
        return InstallPlan(
            state=state,
            wrapper_path=artifacts.wrapper_path,
            record_path=artifacts.record_path,
            settings_path=env.settings_path,
            write_record=False,
            write_wrapper=False,
            write_settings=False,
            blocked=(
                f"Claude settings at {env.settings_path} cannot be read "
                "(permission denied or I/O error); fix the file permissions, "
                "then rerun install."
            ),
            warnings=warnings,
        )
    if env.settings_malformed:
        return InstallPlan(
            state=state,
            wrapper_path=artifacts.wrapper_path,
            record_path=artifacts.record_path,
            settings_path=env.settings_path,
            write_record=False,
            write_wrapper=False,
            write_settings=False,
            blocked=(
                f"Claude settings at {env.settings_path} are not valid JSON; "
                "fix them before installing."
            ),
            warnings=warnings,
        )
    if env.statusline_unsupported:
        # Overwriting a non-object statusLine would record had_statusline=False
        # and make remove delete the key: the user's value would be lost with
        # no restore path. Refuse with the fix in hand instead.
        return InstallPlan(
            state=state,
            wrapper_path=artifacts.wrapper_path,
            record_path=artifacts.record_path,
            settings_path=env.settings_path,
            write_record=False,
            write_wrapper=False,
            write_settings=False,
            blocked=(
                f"statusLine in {env.settings_path} is set to a non-object value that "
                "Dispatch cannot preserve or restore. Change statusLine to an object "
                'like {"type": "command", "command": "..."} (or delete the key), '
                "then rerun install."
            ),
            warnings=warnings,
        )
    points = env.points_at(artifacts.wrapper_path)
    if not points and _invokes_capture_entry_point(
        env.statusline_command(), artifacts.wrapper_path
    ):
        # Installing over a statusline that already invokes a capture entry
        # point would record it as the "original renderer": the wrapper would
        # then delegate back into Dispatch on every refresh.
        return InstallPlan(
            state=state,
            wrapper_path=artifacts.wrapper_path,
            record_path=artifacts.record_path,
            settings_path=env.settings_path,
            write_record=False,
            write_wrapper=False,
            write_settings=False,
            blocked=(
                f"statusLine.command in {env.settings_path} already invokes a Dispatch "
                "usage-capture entry point, so it cannot be recorded as the original "
                "renderer. Point statusLine.command at your real renderer (or remove "
                "the manual Dispatch integration from it), then rerun install."
            ),
            warnings=warnings,
        )
    if (
        not points
        and artifacts.record is not None
        and not _settings_match_original(env, artifacts.record)
    ):
        # Drifted: a valid restoration record exists but settings hold something
        # newer than the recorded original. Re-baselining here would clobber the
        # record with the drifted value, so refuse — mirroring plan_remove.
        return InstallPlan(
            state=state,
            wrapper_path=artifacts.wrapper_path,
            record_path=artifacts.record_path,
            settings_path=env.settings_path,
            write_record=False,
            write_wrapper=False,
            write_settings=False,
            blocked=(
                f"Claude settings at {env.settings_path} changed after install and no "
                "longer match the recorded original statusline; refusing to overwrite "
                "the restoration record. Run `dispatch usage-capture remove --provider "
                "claude --keep-current` to remove the Dispatch artifacts while keeping "
                "the current setting, then rerun install to adopt it as the new original."
            ),
            warnings=warnings,
        )
    if artifacts.record is None and record_too_large(
        _fresh_record(env, artifacts.wrapper_path, None)
    ):
        # The read side rejects records above its size cap, so writing this one
        # would install a wrapper whose original renderer can never be read
        # back: capture-only delegation, a broken status, and no restore path
        # for remove. Block before anything is written instead.
        return InstallPlan(
            state=state,
            wrapper_path=artifacts.wrapper_path,
            record_path=artifacts.record_path,
            settings_path=env.settings_path,
            write_record=False,
            write_wrapper=False,
            write_settings=False,
            blocked=(
                f"the existing statusLine in {env.settings_path} is too large for "
                "Dispatch to preserve in its restoration record (over 64 KiB "
                "serialized), so remove could never restore it. Simplify the "
                "statusLine object, then rerun install."
            ),
            warnings=warnings,
        )
    if points and artifacts.record is None:
        warnings = (
            *warnings,
            "settings already point at the wrapper but no valid restoration record exists; "
            "recording that no original renderer is known.",
        )
    return InstallPlan(
        state=state,
        wrapper_path=artifacts.wrapper_path,
        record_path=artifacts.record_path,
        settings_path=env.settings_path,
        # Never save the Dispatch wrapper itself as the "original", and never
        # overwrite a valid record (the drifted case is blocked above): write
        # one only when no usable record exists at all. The wrapper refresh
        # compares exact bytes so a reinstall silently rebakes a legacy or
        # differently-resolved wrapper to today's content.
        write_record=artifacts.record is None,
        write_wrapper=not artifacts.wrapper_exact,
        write_settings=not points,
        blocked=None,
        warnings=warnings,
    )


def apply_install(
    env: ClaudeStatuslineEnvironment, plan: InstallPlan, *, now: datetime | None = None
) -> None:
    """Execute an install plan in crash-safe order: record, wrapper, settings.

    Settings are revalidated against the plan's environment snapshot before
    anything is written: the record content and the rebuilt settings file both
    derive from that snapshot, so a concurrent edit (for example while the
    interactive confirm prompt was open) must abort rather than be clobbered.
    The check is then repeated immediately before the settings replacement:
    the artifact writes (and their fsyncs) between the up-front check and the
    settings write are their own lost-update window, and a record plus wrapper
    without settings is just the documented ``prepared`` state — recoverable
    by rerunning install — whereas an overwritten concurrent edit is not.
    Artifacts the plan decided *not* to write are revalidated at the same
    point: a prepared-state plan skips both artifact writes, so a concurrent
    remove during the confirm prompt would otherwise leave settings pointing
    at a deleted wrapper with no restoration record.
    """
    if plan.blocked is not None:
        raise RuntimeError(plan.blocked)
    if plan.changes_anything:
        _ensure_settings_unchanged(env)
    ensure_private_dir(plan.wrapper_path.parent)
    if plan.write_record:
        write_usage_capture_record(
            _fresh_record(env, plan.wrapper_path, now), path=plan.record_path
        )
    if plan.write_wrapper:
        write_private_file(plan.wrapper_path, wrapper_content().encode(), mode=0o700)
    if plan.write_settings:
        _ensure_settings_unchanged(env)
        _ensure_planned_artifacts_still_present(plan)
        settings: dict[str, JsonValue] = dict(env.settings) if env.settings is not None else {}
        original = env.statusline
        block: dict[str, JsonValue] = (
            dict(original) if original is not None else {"type": "command"}
        )
        # Claude executes statusLine.command through a shell, so the path must
        # be shell-quoted to survive a DISPATCH_HOME containing spaces or
        # metacharacters. shlex.quote leaves ordinary paths bare, so this is
        # byte-identical to prior installs in the common case.
        block["command"] = shlex.quote(str(plan.wrapper_path))
        settings[STATUSLINE_KEY] = block
        write_claude_settings(env.settings_path, settings)


def _fresh_record(
    env: ClaudeStatuslineEnvironment, wrapper: Path, now: datetime | None
) -> UsageCaptureRecord:
    statusline = None if env.points_at(wrapper) else env.statusline
    return UsageCaptureRecord(
        provider="claude",
        had_statusline=statusline is not None,
        original_statusline=dict(statusline) if statusline is not None else None,
        installed_command=installed_command(),
        installed_at=(now if now is not None else datetime.now(UTC)).isoformat(),
    )


def _ensure_planned_artifacts_still_present(plan: InstallPlan) -> None:
    """Abort the settings write if artifacts the plan skipped have vanished.

    A prepared-state install writes only settings, and a concurrent remove
    while the confirm prompt was open deletes the wrapper and record without
    touching settings — so both settings revalidations still pass. Pointing
    Claude at a deleted wrapper with no restoration record would land straight
    in ``broken``, so re-verify exactly the conditions that justified skipping
    each write: the wrapper still byte-exact and executable, the record still
    readable. Artifacts the plan *did* write were written moments ago by this
    process and are not re-checked.
    """
    wrapper_ok = plan.write_wrapper
    if not wrapper_ok:
        with suppress(OSError):
            wrapper_ok = (
                os.access(plan.wrapper_path, os.X_OK)
                and plan.wrapper_path.read_bytes() == wrapper_content().encode()
            )
    record_ok = plan.write_record or read_usage_capture_record(path=plan.record_path) is not None
    if not (wrapper_ok and record_ok):
        raise ArtifactsChangedError(
            f"Dispatch artifacts under {plan.wrapper_path.parent} changed since "
            "the plan was computed (a concurrent remove may have deleted them); "
            "Claude settings were not modified. Rerun the command."
        )


def _ensure_settings_unchanged(env: ClaudeStatuslineEnvironment) -> None:
    """Abort if the settings file no longer parses to the plan's snapshot.

    A file that was malformed at plan time and is still malformed passes even
    if its bytes changed: malformed settings cannot point at the wrapper, and
    ``remove --keep-current`` is documented as the cleanup path for exactly
    that state. An unreadable file never passes — not even under that
    carve-out — because content that cannot be read cannot be proven safe.
    """
    current = load_settings(env.settings_path)
    if current.unreadable:
        raise SettingsChangedError(
            f"Claude settings at {env.settings_path} became unreadable since the "
            "plan was computed; nothing was written. Fix the file permissions "
            "and rerun the command."
        )
    if current.malformed and env.settings_malformed:
        return
    if current.malformed or current.data != env.settings:
        raise SettingsChangedError(
            f"Claude settings at {env.settings_path} changed since the plan was "
            "computed; nothing was written. Rerun the command."
        )


@dataclass(frozen=True)
class RemovePlan:
    state: LifecycleState
    wrapper_path: Path
    record_path: Path
    settings_path: Path
    restore_settings: bool
    delete_wrapper: bool
    delete_record: bool
    keep_current: bool
    blocked: str | None
    nothing_to_remove: bool
    record: UsageCaptureRecord | None

    @property
    def changes_anything(self) -> bool:
        return self.restore_settings or self.delete_wrapper or self.delete_record


def plan_remove(
    env: ClaudeStatuslineEnvironment,
    *,
    directory: Path | None = None,
    keep_current: bool = False,
) -> RemovePlan:
    """Decide what remove must do; refuses drifted settings unless kept."""
    artifacts = _inspect_artifacts(directory)
    state = _state(env, artifacts)
    points = env.points_at(artifacts.wrapper_path)
    blocked: str | None = None
    restore_settings = False
    if not points and not artifacts.wrapper_exists and not artifacts.record_exists:
        return RemovePlan(
            state=state,
            wrapper_path=artifacts.wrapper_path,
            record_path=artifacts.record_path,
            settings_path=env.settings_path,
            restore_settings=False,
            delete_wrapper=False,
            delete_record=False,
            keep_current=keep_current,
            blocked=None,
            nothing_to_remove=True,
            record=None,
        )
    if env.settings_unreadable:
        # Unlike malformed content (which provably cannot point at the
        # wrapper), an unreadable file may still point at it — deleting the
        # wrapper and record here could leave Claude invoking a dead command
        # with the restoration record gone. Block both modes, keep-current
        # included.
        blocked = (
            f"Claude settings at {env.settings_path} cannot be read (permission "
            "denied or I/O error), so remove cannot verify whether they still "
            "point at the Dispatch wrapper; fix the file permissions, then "
            "rerun remove."
        )
    elif env.settings_malformed:
        if not keep_current:
            blocked = (
                f"Claude settings at {env.settings_path} are not valid JSON; fix them first, "
                "or rerun with --keep-current to remove only the Dispatch artifacts."
            )
    elif points:
        if keep_current:
            blocked = (
                "--keep-current would leave Claude settings pointing at the deleted wrapper; "
                "run remove without it to restore the original statusline."
            )
        elif artifacts.record is None:
            blocked = (
                "restoration record is missing or invalid, so the original statusline cannot "
                f"be restored. Fix statusLine in {env.settings_path} manually, then rerun "
                "remove --keep-current to clean up the Dispatch artifacts."
            )
        else:
            restore_settings = True
    elif not keep_current and not _settings_match_original(env, artifacts.record):
        blocked = (
            "Claude settings no longer point at the Dispatch wrapper (they changed after "
            "install); refusing to modify them. Rerun with --keep-current to remove the "
            "Dispatch artifacts and keep the current statusline setting."
        )
    return RemovePlan(
        state=state,
        wrapper_path=artifacts.wrapper_path,
        record_path=artifacts.record_path,
        settings_path=env.settings_path,
        restore_settings=restore_settings,
        delete_wrapper=artifacts.wrapper_exists,
        delete_record=artifacts.record_exists,
        keep_current=keep_current,
        blocked=blocked,
        nothing_to_remove=False,
        record=artifacts.record,
    )


def apply_remove(env: ClaudeStatuslineEnvironment, plan: RemovePlan) -> None:
    """Execute a remove plan: settings restored first, artifacts deleted after.

    Like install, the settings file is revalidated against the plan's snapshot
    before anything changes — including keep-current and prepared removals
    that only delete artifacts, where a concurrent edit pointing settings back
    at the wrapper would otherwise leave Claude invoking a deleted command
    with the restoration record gone. Unlike install, the settings write is
    the first mutation after that check — no artifact writes intervene — so a
    single adjacent revalidation suffices here.
    """
    if plan.blocked is not None:
        raise RuntimeError(plan.blocked)
    if plan.changes_anything:
        _ensure_settings_unchanged(env)
    if plan.restore_settings:
        settings: dict[str, JsonValue] = dict(env.settings) if env.settings is not None else {}
        original = (
            plan.record.original_statusline
            if plan.record is not None and plan.record.had_statusline
            else None
        )
        if original is None:
            settings.pop(STATUSLINE_KEY, None)
        else:
            settings[STATUSLINE_KEY] = dict(original)
        if settings or env.settings is not None:
            write_claude_settings(env.settings_path, settings)
    if plan.delete_wrapper:
        plan.wrapper_path.unlink(missing_ok=True)
    if plan.delete_record:
        plan.record_path.unlink(missing_ok=True)
    with suppress(OSError):
        plan.wrapper_path.parent.rmdir()
