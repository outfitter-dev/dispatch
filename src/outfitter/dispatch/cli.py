"""dispatch CLI entrypoint.

Phase 0 stub: a Typer app that resolves and renders help. In Phase 2 this app is
replaced by ``surfaces.derive_cli(registry)`` — one command per authored op, no
hand-written per-op commands (see ``.claude/rules/surfaces.md``).
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="dispatch",
    help="Local control plane for orchestrating Codex agent lanes.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """dispatch — orchestrate Codex lanes over the App Server."""


if __name__ == "__main__":
    app()
