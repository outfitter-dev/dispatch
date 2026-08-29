"""`dispatch usage-capture` surface controls.

Host-machine statusline integration, daemon-free by contract: none of these
commands touch the control socket. `run` is the high-frequency delegation
path; `install`/`status`/`remove` manage the wrapper, the restoration record,
and Claude Code's `statusLine.command` with explicit consent.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Annotated, Literal

import typer

from outfitter.dispatch.core.usage_capture_lifecycle import (
    ArtifactsChangedError,
    InstallPlan,
    RemovePlan,
    SettingsChangedError,
    UsageCaptureStatus,
)

_PROVIDER_OPTION = Annotated[
    Literal["claude"],
    typer.Option("--provider", help="Provider whose statusline integration to manage."),
]
_DRY_RUN_OPTION = Annotated[
    bool, typer.Option("--dry-run", help="Show the plan without changing anything.")
]
_YES_OPTION = Annotated[
    bool,
    typer.Option("--yes", help="Confirm changing Claude settings without prompting."),
]

_STATE_COLORS = {
    "installed": typer.colors.GREEN,
    "prepared": typer.colors.YELLOW,
    "not_installed": typer.colors.YELLOW,
    "drifted": typer.colors.YELLOW,
    "disabled": typer.colors.YELLOW,
    "broken": typer.colors.RED,
}


def build_usage_capture_cli() -> typer.Typer:
    app = typer.Typer(
        no_args_is_help=True,
        add_completion=False,
        help="Manage host-side provider usage capture (daemon-free).",
    )

    @app.command(
        name="run",
        help=(
            "Capture provider usage from the statusline JSON on stdin, then delegate "
            "to the original renderer with verbatim stdout passthrough."
        ),
    )
    def _run(
        provider: Annotated[
            Literal["claude"],
            typer.Option("--provider", help="Provider whose statusline payload is on stdin."),
        ],
    ) -> None:
        from outfitter.dispatch.core.usage_capture_run import run_usage_capture_from_stdin

        raise typer.Exit(code=run_usage_capture_from_stdin())

    @app.command(
        name="install",
        help=(
            "Point Claude Code's statusLine.command at the Dispatch capture wrapper, "
            "preserving the original renderer in a restoration record."
        ),
    )
    def _install(
        provider: _PROVIDER_OPTION,
        dry_run: _DRY_RUN_OPTION = False,
        yes: _YES_OPTION = False,
    ) -> None:
        from outfitter.dispatch.core.usage_capture_lifecycle import apply_install, plan_install
        from outfitter.dispatch.core.usage_capture_settings import inspect_claude_environment

        env = inspect_claude_environment()
        plan = plan_install(env)
        _echo_lines(_describe_install(plan))
        _echo_warnings(plan.warnings)
        _exit_if_blocked(plan.blocked)
        if dry_run:
            typer.echo("dry run: no changes made")
            return
        if not plan.changes_anything:
            return
        if plan.write_settings:
            _confirm(yes, prompt=f"Replace statusLine.command in {plan.settings_path}?")
        _apply_or_exit(lambda: apply_install(env, plan))
        typer.echo("install complete" if plan.write_settings else "artifacts refreshed")

    @app.command(
        name="status",
        help="Report the statusline capture lifecycle state without exposing the original command.",
    )
    def _status(
        provider: _PROVIDER_OPTION,
        json_output: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        from outfitter.dispatch.core.usage_capture_lifecycle import usage_capture_status
        from outfitter.dispatch.core.usage_capture_settings import inspect_claude_environment

        status = usage_capture_status(inspect_claude_environment())
        if json_output:
            typer.echo(status.model_dump_json(indent=2))
            return
        _echo_lines(_describe_status(status))

    @app.command(
        name="remove",
        help=(
            "Restore the original Claude statusline setting and delete the Dispatch "
            "wrapper and restoration record."
        ),
    )
    def _remove(
        provider: _PROVIDER_OPTION,
        dry_run: _DRY_RUN_OPTION = False,
        yes: _YES_OPTION = False,
        keep_current: Annotated[
            bool,
            typer.Option(
                "--keep-current",
                help=(
                    "Remove the Dispatch artifacts but leave the current Claude statusline "
                    "setting untouched (cleanup for drifted settings)."
                ),
            ),
        ] = False,
    ) -> None:
        from outfitter.dispatch.core.usage_capture_lifecycle import apply_remove, plan_remove
        from outfitter.dispatch.core.usage_capture_settings import inspect_claude_environment

        env = inspect_claude_environment()
        plan = plan_remove(env, keep_current=keep_current)
        _echo_lines(_describe_remove(plan))
        _exit_if_blocked(plan.blocked)
        if plan.nothing_to_remove:
            typer.echo("nothing to remove")
            return
        if dry_run:
            typer.echo("dry run: no changes made")
            return
        if not plan.changes_anything:
            typer.echo("nothing to remove")
            return
        prompt = (
            f"Restore the original statusLine in {plan.settings_path} and delete the "
            "Dispatch wrapper and restoration record?"
            if plan.restore_settings
            else "Delete the Dispatch wrapper and restoration record?"
        )
        _confirm(yes, prompt=prompt)
        _apply_or_exit(lambda: apply_remove(env, plan))
        typer.echo("remove complete")

    return app


def _apply_or_exit(apply: Callable[[], None]) -> None:
    """Run an apply step, projecting expected failures to a clean exit 1.

    Concurrency aborts (settings or artifacts changed under the plan) and
    filesystem failures (an unwritable artifact directory or settings file
    raising ``OSError``/``PermissionError`` from the lifecycle writes) are
    expected boundary outcomes, not crashes: the surface projects them as an
    actionable stderr line, never a traceback.
    """
    try:
        apply()
    except (ArtifactsChangedError, SettingsChangedError) as exc:
        typer.secho(f"dispatch: {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        target = exc.filename if exc.filename is not None else "a lifecycle path"
        reason = exc.strerror if exc.strerror is not None else str(exc)
        typer.secho(
            f"dispatch: {target}: {reason}; check file and directory "
            "permissions, then rerun the command",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=1) from exc


def _confirm(yes: bool, *, prompt: str) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        typer.secho(
            "dispatch: this changes Claude settings; pass --yes when not running interactively",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=2)
    typer.confirm(prompt, abort=True, err=True)


def _exit_if_blocked(blocked: str | None) -> None:
    if blocked is not None:
        typer.secho(f"dispatch: {blocked}", fg="red", err=True)
        raise typer.Exit(code=1)


def _echo_lines(lines: tuple[str, ...]) -> None:
    for line in lines:
        typer.echo(line)


def _echo_warnings(warnings: tuple[str, ...]) -> None:
    for warning in warnings:
        typer.secho(f"warning: {warning}", fg="yellow", err=True)


def _describe_install(plan: InstallPlan) -> tuple[str, ...]:
    lines = [f"state: {plan.state}"]
    if plan.write_record:
        lines.append(f"will write restoration record: {plan.record_path}")
    if plan.write_wrapper:
        lines.append(f"will write wrapper: {plan.wrapper_path}")
    if plan.write_settings:
        lines.append(f"will set statusLine.command in {plan.settings_path} to the wrapper")
    if plan.blocked is None and not plan.changes_anything:
        lines.append("already installed; nothing to do")
    return tuple(lines)


def _describe_remove(plan: RemovePlan) -> tuple[str, ...]:
    lines = [f"state: {plan.state}"]
    if plan.restore_settings:
        lines.append(f"will restore the original statusLine in {plan.settings_path}")
    elif plan.keep_current and plan.changes_anything:
        lines.append(f"will keep the current statusLine in {plan.settings_path} untouched")
    if plan.delete_wrapper:
        lines.append(f"will delete wrapper: {plan.wrapper_path}")
    if plan.delete_record:
        lines.append(f"will delete restoration record: {plan.record_path}")
    return tuple(lines)


def _describe_status(status: UsageCaptureStatus) -> tuple[str, ...]:
    state = typer.style(status.state, fg=_STATE_COLORS.get(status.state))
    wrapper = (
        "present, executable, current"
        if status.wrapper_current
        else "present, stale or not executable"
        if status.wrapper_exists
        else "missing"
    )
    if status.wrapper_missing_executable is not None:
        wrapper = (
            f"present, but its dispatch executable {status.wrapper_missing_executable} "
            "is missing; rerun install to rebake it"
        )
    record = (
        f"valid (original renderer {'recorded' if status.original_renderer_recorded else 'none'})"
        if status.record_valid
        else "invalid"
        if status.record_exists
        else "missing"
    )
    settings = (
        "points at wrapper" if status.settings_points_at_wrapper else "does not point at wrapper"
    )
    if status.settings_statusline_unsupported:
        settings = "statusLine is a non-object value"
    if status.settings_malformed:
        settings = "malformed JSON"
    if status.settings_unreadable:
        settings = "unreadable (check file permissions)"
    capture = "none recorded"
    if status.last_capture_at is not None:
        capture = f"{status.last_capture_at} ({'fresh' if status.capture_fresh else 'stale'})"
    lines = [
        f"state: {state}",
        f"settings: {status.settings_path} ({settings})",
        f"wrapper: {status.wrapper_path} ({wrapper})",
        f"record: {record}",
        f"last capture: {capture}",
    ]
    if status.disable_all_hooks:
        lines.append("constraint: disableAllHooks is enabled in Claude settings")
    lines.extend(
        f"constraint: statusLine overridden by higher-precedence {path}"
        for path in status.override_paths
    )
    if not status.dispatch_on_path:
        lines.append("constraint: `dispatch` is not resolvable on PATH")
    return tuple(lines)
