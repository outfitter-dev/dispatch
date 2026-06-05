"""CLI surface: a Typer app derived from the op registry whose commands route to
the daemon over the control socket (sync client; never imports the app-server).
"""

from __future__ import annotations

import json
import socket
from functools import partial
from pathlib import Path
from typing import Annotated

import typer

from outfitter.dispatch import config
from outfitter.dispatch.contracts.derive_cli import derive_cli
from outfitter.dispatch.version import package_version


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

    # `up`/`down` manage the daemon PROCESS (not ops, which run inside it).
    @app.command(name="up", help="Start the daemon (detached singleton).")
    def _up() -> None:
        from outfitter.dispatch.daemon import lifecycle

        config.ensure_base()
        try:
            started = lifecycle.start_detached(path, config.pidfile_path())
        except (RuntimeError, TimeoutError) as exc:
            typer.secho(f"dispatch: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo("dispatchd started" if started else "dispatchd already running")

    @app.command(name="down", help="Stop the daemon.")
    def _down() -> None:
        from outfitter.dispatch.daemon import lifecycle

        stopped = lifecycle.stop_daemon(path, config.pidfile_path())
        typer.echo("dispatchd stopped" if stopped else "dispatchd not running")

    return app
