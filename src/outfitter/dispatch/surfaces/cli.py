"""CLI surface: a Typer app derived from the op registry whose commands route to
the daemon over the control socket (sync client; never imports the app-server).
"""

from __future__ import annotations

import json
import socket
import sqlite3
from functools import partial
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from click.core import Command
from click.shell_completion import get_completion_class
from typer.main import get_command

from outfitter.dispatch import config
from outfitter.dispatch.contracts.derive_cli import derive_cli
from outfitter.dispatch.version import package_version

CLI_SURFACE_CONTROL_PATHS: tuple[tuple[str, ...], ...] = (
    ("doctor",),
    ("mcp",),
    ("completion",),
    ("up",),
    ("down",),
    ("registry", "migrate"),
)


def _recv_line(sock: socket.socket) -> bytes:
    buffer = bytearray()
    while b"\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer.extend(chunk)
    return bytes(buffer)


def invoke_daemon(socket_path: Path, op_id: str, params: dict[str, object]) -> dict[str, object]:
    """Send one control request and return its result, or exit with the projected
    code on error (the CLI's projection of the DispatchError taxonomy)."""
    request = json.dumps({"id": 1, "method": op_id, "params": params}) + "\n"
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
    error = message.get("error")
    if isinstance(error, dict):
        data = error.get("data")
        exit_code = data.get("exitCode") if isinstance(data, dict) else None
        typer.secho(f"dispatch: {error.get('message')}", fg="red", err=True)
        raise typer.Exit(code=exit_code if isinstance(exit_code, int) else 1)
    result = message.get("result")
    return result if isinstance(result, dict) else {}


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"dispatch {package_version()}")
        raise typer.Exit()


def build_cli(socket_path: Path | None = None) -> typer.Typer:
    path = socket_path if socket_path is not None else config.socket_path()
    # Import the registry lazily so this module stays a thin surface.
    from outfitter.dispatch.core.ops import REGISTRY

    app = derive_cli(REGISTRY, partial(invoke_daemon, path))

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
