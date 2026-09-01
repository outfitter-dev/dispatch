"""CLI surface: a Typer app derived from the op registry whose commands route to
the daemon over the control socket (sync client; never imports the app-server).
"""

from __future__ import annotations

import json
import socket
import sqlite3
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from click.core import Command
from click.shell_completion import get_completion_class
from typer.main import get_command

from outfitter.dispatch import config
from outfitter.dispatch.contracts.derive_cli import derive_cli
from outfitter.dispatch.contracts.registry import CONTROL_META_METHOD, prehandshake_op_allowed
from outfitter.dispatch.version import package_version

CLI_SURFACE_CONTROL_PATHS: tuple[tuple[str, ...], ...] = (
    ("doctor",),
    ("mcp",),
    ("completion",),
    ("up",),
    ("down",),
    ("registry", "migrate"),
)
_METHOD_NOT_FOUND = -32601


def _recv_line(sock: socket.socket) -> bytes:
    buffer = bytearray()
    while b"\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer.extend(chunk)
    return bytes(buffer)


def invoke_daemon(
    socket_path: Path,
    current_ops: frozenset[str],
    read_safe_ops: frozenset[str],
    legacy_safe_ops: frozenset[str],
    op_schema_hashes: Callable[[], dict[str, str]],
    op_id: str,
    params: dict[str, object],
    *,
    retry_on_stale: bool = True,
) -> dict[str, object]:
    """Send one control request and return its result, or exit with the projected
    code on error (the CLI's projection of the DispatchError taxonomy).

    Before forwarding, verify the daemon's fingerprint for THIS op matches this
    CLI's. A stale daemon parses op input with Pydantic's default
    ``extra="ignore"``, so a field it does not know (e.g. ``provider``) would be
    dropped silently — never forward input a skewed daemon could misread. Ops
    whose schemas match are forwarded even when other ops drifted, so a busy
    stale daemon can still be inspected and drained safely. A pre-handshake
    daemon (reports a version but no hashes) still accepts ``read_safe_ops``
    (derived: unshared-input read ops, see :func:`registry_read_safe_ops`)
    when it self-reports at least the read baseline floor — read inputs
    evolved too, so older daemons are blocked — and ``legacy_safe_ops``
    (schema hash matching the parent release's checked-in baseline, see
    :func:`registry_legacy_safe_ops`) only when it self-reports exactly the
    parent version — the baseline proves nothing about older daemons.
    Everything else is blocked: the ops whose schemas drifted since the
    parent release (``new``/``new-plan``), and EVERY op on a daemon so old it
    lacks ``__dispatch/metadata`` entirely (<= 0.8.1).
    """
    skew = _daemon_op_skew(
        socket_path,
        op_id,
        op_schema_hashes().get(op_id),
        read_safe=op_id in read_safe_ops,
        baseline_safe=op_id in legacy_safe_ops,
    )
    if skew is not None:
        if retry_on_stale and _restart_stale_daemon_if_idle(socket_path, skew):
            return invoke_daemon(
                socket_path,
                current_ops,
                read_safe_ops,
                legacy_safe_ops,
                op_schema_hashes,
                op_id,
                params,
                retry_on_stale=False,
            )
        typer.secho(
            f"dispatch: {skew}; run `dispatch down && dispatch up`, then retry.",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=8)
    message = _control_request(socket_path, op_id, params)
    error = message.get("error")
    if isinstance(error, dict):
        if (
            retry_on_stale
            and op_id in current_ops
            and _is_method_not_found(error)
            and _restart_stale_daemon_if_idle(
                socket_path, f"daemon does not support current CLI op {op_id!r}"
            )
        ):
            return invoke_daemon(
                socket_path,
                current_ops,
                read_safe_ops,
                legacy_safe_ops,
                op_schema_hashes,
                op_id,
                params,
                retry_on_stale=False,
            )
        data = error.get("data")
        exit_code = data.get("exitCode") if isinstance(data, dict) else None
        typer.secho(f"dispatch: {error.get('message')}", fg="red", err=True)
        raise typer.Exit(code=exit_code if isinstance(exit_code, int) else 1)
    result = message.get("result")
    return result if isinstance(result, dict) else {}


def _control_request(
    socket_path: Path, method: str, params: dict[str, object]
) -> dict[str, object]:
    request = json.dumps({"id": 1, "method": method, "params": params}) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(30.0)  # never hang on a dead/half-writing daemon (mirrors MCP)
            sock.connect(str(socket_path))
            sock.sendall(request.encode())
            line = _recv_line(sock)
    except OSError as exc:
        typer.secho(
            f"dispatch: cannot reach daemon at {socket_path} ({exc}). Is dispatchd running?",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=8) from exc

    try:
        message: object = json.loads(line)
    except json.JSONDecodeError as exc:
        detail = "connection closed" if not line else "malformed response"
        typer.secho(f"dispatch: no usable response from daemon ({detail})", fg="red", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(message, dict):
        typer.secho("dispatch: malformed response from daemon", fg="red", err=True)
        raise typer.Exit(code=1)
    return message


def _is_method_not_found(error: dict[str, object]) -> bool:
    return error.get("code") == _METHOD_NOT_FOUND


def _daemon_op_skew(
    socket_path: Path,
    op_id: str,
    expected_hash: str | None,
    *,
    read_safe: bool,
    baseline_safe: bool,
) -> str | None:
    """Describe daemon/CLI schema skew for ONE op, or ``None`` when safe to send.

    Only this op's fingerprint gates the call: whole-registry drift alone never
    blocks an op whose own schema matches, so hash-matching ops (status, roster,
    stop, ...) stay usable against a busy stale daemon. A daemon that predates
    the handshake reports no hashes; ``read_safe`` ops pass when it
    self-reports at least the read baseline floor, ``baseline_safe`` ops
    (schema unchanged since the parent release, such as ``stop``) only when
    it self-reports exactly the parent version, and a daemon reporting no
    version at all (no metadata method, <= 0.8.1) gets nothing (see
    :func:`prehandshake_op_allowed`). Everything else — notably the ops whose
    schema drifted, like ``new``/``new-plan`` with ``provider`` — is treated as
    skewed. Any other probe failure also returns ``None`` — the op call that
    follows surfaces it with the normal projection.
    """
    message = _control_request(socket_path, CONTROL_META_METHOD, {})
    error = message.get("error")
    if isinstance(error, dict):
        if _is_method_not_found(error) and not prehandshake_op_allowed(
            None, read_safe=read_safe, baseline_safe=baseline_safe
        ):
            return "daemon predates the op-schema handshake (older than this CLI)"
        return None
    result = message.get("result")
    op_schemas = result.get("op_schemas") if isinstance(result, dict) else None
    if not isinstance(op_schemas, dict):
        reported = result.get("version") if isinstance(result, dict) else None
        if prehandshake_op_allowed(reported, read_safe=read_safe, baseline_safe=baseline_safe):
            return None
        version = f"version {reported}" if isinstance(reported, str) else "unreported version"
        return f"daemon predates the op-schema handshake ({version}, older than this CLI)"
    if op_schemas.get(op_id) == expected_hash:
        return None
    return f"daemon op schemas do not match this CLI for op {op_id!r} (stale daemon)"


def _restart_stale_daemon_if_idle(socket_path: Path, problem: str) -> bool:
    idle, reason = _daemon_idle_for_restart(socket_path)
    if not idle:
        typer.secho(
            (
                f"dispatch: {problem}; "
                f"not restarting automatically because {reason}. "
                "Run `dispatch down && dispatch up`, then retry."
            ),
            fg="red",
            err=True,
        )
        raise typer.Exit(code=8)

    from outfitter.dispatch.daemon import lifecycle

    pidfile = config.pidfile_path()
    try:
        stopped = lifecycle.stop_daemon(socket_path, pidfile)
        if not stopped:
            typer.secho(
                (
                    f"dispatch: {problem}; "
                    "could not identify a live daemon process to restart safely. "
                    "Run `dispatch down && dispatch up`, then retry."
                ),
                fg="red",
                err=True,
            )
            raise typer.Exit(code=8)
        lifecycle.start_detached(socket_path, pidfile)
    except typer.Exit:
        raise
    except (RuntimeError, TimeoutError, OSError) as exc:
        typer.secho(
            (
                f"dispatch: {problem}; "
                f"restart failed ({exc}). Run `dispatch down && dispatch up`, then retry."
            ),
            fg="red",
            err=True,
        )
        raise typer.Exit(code=8) from exc
    typer.secho(
        f"dispatch: restarted idle stale daemon ({problem}); retrying.",
        fg="yellow",
        err=True,
    )
    return True


def _daemon_idle_for_restart(socket_path: Path) -> tuple[bool, str]:
    try:
        status_message = _control_request(socket_path, "status", {})
    except typer.Exit:
        return False, "daemon status could not be read"
    if isinstance(status_message.get("error"), dict):
        return False, "daemon status could not be read"
    result = status_message.get("result")
    if not isinstance(result, dict):
        return False, "daemon status response was malformed"

    active = result.get("active")
    if isinstance(active, int):
        return (active == 0, "daemon has active work" if active else "daemon is idle")

    lanes = result.get("lanes")
    idle = result.get("idle")
    busy = result.get("busy")
    if isinstance(lanes, int) and isinstance(idle, int) and isinstance(busy, int):
        if busy > 0:
            return False, "daemon has busy lanes"
        if lanes == idle:
            return True, "daemon is idle"
        return False, "daemon has non-idle lanes"
    return False, "daemon activity is unknown"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"dispatch {package_version()}")
        raise typer.Exit()


def build_cli(socket_path: Path | None = None) -> typer.Typer:
    path = socket_path if socket_path is not None else config.socket_path()
    # Import the registry lazily so this module stays a thin surface.
    from outfitter.dispatch.contracts.registry import (
        registry_legacy_safe_ops,
        registry_op_schema_hashes,
        registry_read_safe_ops,
    )
    from outfitter.dispatch.core.ops import REGISTRY

    current_ops = frozenset(REGISTRY.ids())
    read_safe_ops = registry_read_safe_ops(REGISTRY)
    legacy_safe_ops = registry_legacy_safe_ops(REGISTRY)
    # Bind the hashes lazily: they are only needed when an op is actually
    # invoked, and computing them (JSON schemas for every op) would tax
    # --help/completion.
    app = derive_cli(
        REGISTRY,
        partial(
            invoke_daemon,
            path,
            current_ops,
            read_safe_ops,
            legacy_safe_ops,
            partial(registry_op_schema_hashes, REGISTRY),
        ),
    )

    @app.callback()
    def _root(
        version: Annotated[
            bool,
            typer.Option(
                "--version",
                callback=_version_callback,
                help="Show the installed dispatch version and exit.",
                is_eager=True,
            ),
        ] = False,
    ) -> None:
        pass

    # `dispatch mcp` is a surface launcher, not an op: it serves the same registry
    # over MCP stdio, routing tool calls to the same daemon.
    @app.command(name="mcp", help="Serve the op registry as an MCP stdio server.")
    def _mcp() -> None:
        from outfitter.dispatch.surfaces.mcp import run_mcp

        run_mcp(path)

    @app.command(name="completion", help="Print a shell completion script.")
    def _completion(
        shell: Annotated[
            Literal["bash", "zsh", "fish"],
            typer.Argument(help="Shell to generate completions for."),
        ],
    ) -> None:
        completion_cls = get_completion_class(shell)
        if completion_cls is None:
            typer.secho(f"dispatch: unsupported shell {shell!r}", fg="red", err=True)
            raise typer.Exit(code=2)
        click_command = cast(Command, get_command(app))
        complete = completion_cls(
            click_command,
            {},
            "dispatch",
            "_DISPATCH_COMPLETE",
        )
        typer.echo(complete.source())

    @app.command(name="doctor", help="Diagnose install, daemon, registry, and app-server health.")
    def _doctor(
        json_output: Annotated[
            bool, typer.Option("--json/--text", help="Render machine-readable JSON output.")
        ] = True,
        app_server: Annotated[
            bool,
            typer.Option(
                "--app-server/--no-app-server",
                help="Run a low-risk codex app-server initialize smoke.",
            ),
        ] = True,
        timeout: Annotated[
            float, typer.Option(help="Seconds to wait for app-server startup/initialize.")
        ] = 10.0,
    ) -> None:
        from outfitter.dispatch.doctor import DoctorOptions, render_text, run_doctor

        report = run_doctor(DoctorOptions(app_server=app_server, timeout=timeout))
        if json_output:
            typer.echo(report.model_dump_json(indent=2))
        else:
            typer.echo(render_text(report))
        if report.status == "fail":
            raise typer.Exit(code=8)

    # `up`/`down` manage the daemon PROCESS (not ops, which run inside it).
    @app.command(name="up", help="Start the daemon (detached singleton).")
    def _up(
        json_output: Annotated[
            bool, typer.Option("--json/--text", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        from outfitter.dispatch.daemon import lifecycle

        config.ensure_base()
        try:
            started = lifecycle.start_detached(path, config.pidfile_path())
        except (RuntimeError, TimeoutError) as exc:
            typer.secho(f"dispatch: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "status": "started" if started else "already_running",
                        "started": started,
                        "socket": str(path),
                        "pidfile": str(config.pidfile_path()),
                    },
                    indent=2,
                )
            )
        else:
            typer.echo("dispatchd started" if started else "dispatchd already running")

    @app.command(name="down", help="Stop the daemon.")
    def _down(
        json_output: Annotated[
            bool, typer.Option("--json/--text", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        from outfitter.dispatch.daemon import lifecycle

        stopped = lifecycle.stop_daemon(path, config.pidfile_path())
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "status": "stopped" if stopped else "not_running",
                        "stopped": stopped,
                        "socket": str(path),
                        "pidfile": str(config.pidfile_path()),
                    },
                    indent=2,
                )
            )
        else:
            typer.echo("dispatchd stopped" if stopped else "dispatchd not running")

    registry = typer.Typer(no_args_is_help=True, add_completion=False)
    app.add_typer(registry, name="registry")

    @registry.command(name="migrate", help="Apply registry compatibility migrations safely.")
    def _registry_migrate(
        json_output: Annotated[
            bool, typer.Option("--json/--text", help="Render machine-readable JSON output.")
        ] = True,
        backup: Annotated[
            bool,
            typer.Option(
                "--backup/--no-backup",
                help="Back up the registry before migrating.",
            ),
        ] = True,
        allow_running: Annotated[
            bool,
            typer.Option(
                "--allow-running",
                help=(
                    "Allow migration while dispatchd is reachable. "
                    "Use only for controlled recovery."
                ),
            ),
        ] = False,
    ) -> None:
        import asyncio
        import shutil
        from datetime import UTC, datetime

        from outfitter.dispatch.daemon import lifecycle
        from outfitter.dispatch.registry.store import SCHEMA_VERSION, Registry

        config.ensure_base()
        db = config.db_path()
        running = lifecycle.is_daemon_up(path)
        if running and not allow_running:
            recovery = "Run `dispatch down`, then `dispatch registry migrate`, then `dispatch up`."
            payload = {
                "status": "blocked",
                "migrated": False,
                "reason": "daemon_running",
                "recovery": recovery,
                "db": str(db),
            }
            if json_output:
                typer.echo(json.dumps(payload, indent=2))
            else:
                typer.secho(recovery, fg="red", err=True)
            raise typer.Exit(code=8)

        before = _registry_version(db)
        backup_path: str | None = None
        if backup and db.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            backup_file = db.with_name(f"{db.name}.bak-{stamp}")
            shutil.copy2(db, backup_file)
            backup_path = str(backup_file)

        async def _migrate() -> None:
            store = await Registry.open(db)
            await store.close()

        try:
            asyncio.run(_migrate())
        except RuntimeError as exc:
            payload = {
                "status": "failed",
                "migrated": False,
                "reason": str(exc),
                "db": str(db),
                "from_schema_version": before,
                "to_schema_version": SCHEMA_VERSION,
                "backup": backup_path,
            }
            if json_output:
                typer.echo(json.dumps(payload, indent=2))
            else:
                typer.secho(f"dispatch: {exc}", fg="red", err=True)
            raise typer.Exit(code=8) from exc

        after = _registry_version(db)
        payload = {
            "status": "ok",
            "migrated": before != after,
            "db": str(db),
            "from_schema_version": before,
            "to_schema_version": after,
            "backup": backup_path,
            "daemon_running": running,
        }
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(
                f"registry schema {before if before is not None else 'none'} -> {after}; "
                f"backup: {backup_path or 'none'}"
            )

    return app


def _registry_version(path: Path) -> int | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else None
