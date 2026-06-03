"""``dispatchd`` entrypoint.

Phase 0 stub: a Typer app that resolves and renders help so the console script
``dispatchd`` exists. The real daemon lifecycle (app-server supervision,
control socket, ``up``/``down``/``status``) lands in Phase 5.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="dispatchd",
    help="dispatch daemon — owns the Codex app-server and the core.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """dispatchd — the long-lived dispatch daemon host."""


def main() -> None:
    """Console-script entrypoint for ``dispatchd``."""
    app()


if __name__ == "__main__":
    main()
