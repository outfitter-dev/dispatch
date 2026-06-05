"""``dispatchd`` entrypoint.

The long-lived daemon host. ``dispatchd run`` owns the app-server and serves the
control socket; ``up``/``down``/``status`` (Phase 5) wrap it with supervision.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from outfitter.dispatch import config
from outfitter.dispatch.version import package_version

from .host import run_daemon

app = typer.Typer(
    name="dispatchd",
    help="dispatch daemon — owns the Codex app-server and the core.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"dispatchd {package_version()}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            help="Show the installed dispatch daemon version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """dispatchd — the long-lived dispatch daemon host."""


@app.command()
def run() -> None:
    """Own the app-server and serve the control socket (foreground)."""
    config.ensure_base()
    asyncio.run(run_daemon(config.socket_path(), config.db_path()))


def main() -> None:
    """Console-script entrypoint for ``dispatchd``."""
    app()


if __name__ == "__main__":
    main()
