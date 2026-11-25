"""Explicit daemon-free Claude owner integration for Outpost response wakes."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated

import httpx
import typer

from outfitter.dispatch.core.outpost_models import OutpostRunnerError
from outfitter.dispatch.core.outpost_recovery import reconcile_run
from outfitter.dispatch.core.outpost_runner import run_outpost_owner, set_run_paused


def build_outpost_cli() -> typer.Typer:
    app = typer.Typer(no_args_is_help=True, add_completion=False)

    @app.command("run")
    def run(
        api_url: Annotated[str, typer.Option(help="Outpost API base including /api/v1.")],
        mcp_url: Annotated[str, typer.Option(help="Outpost MCP endpoint for the same deployment.")],
        run_dir: Annotated[
            Path, typer.Option(help="New owner-only directory for binding and receipts.")
        ],
        cwd: Annotated[Path, typer.Option(exists=True, file_okay=False, resolve_path=True)],
        prompt_file: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
        model: Annotated[str | None, typer.Option(help="Claude model for this owner.")] = None,
        max_seconds: Annotated[float, typer.Option(min=1, max=86400)] = 300,
    ) -> None:
        """Own a fresh Claude process until the bounded run window ends."""
        token = os.environ.get("OUTPOST_TOKEN", "")
        if not token:
            raise typer.BadParameter("OUTPOST_TOKEN must contain an existing session-enabled token")
        try:
            asyncio.run(
                run_outpost_owner(
                    api_url=api_url,
                    mcp_url=mcp_url,
                    run_dir=run_dir,
                    cwd=cwd,
                    prompt=prompt_file.read_text(),
                    token=token,
                    model=model,
                    max_seconds=max_seconds,
                    report=typer.echo,
                )
            )
        except (OutpostRunnerError, ValueError, OSError, httpx.HTTPError) as exc:
            # HTTP errors can contain request URLs. Report fixed diagnostics only.
            if isinstance(exc, httpx.HTTPError):
                typer.echo("Outpost runner could not authenticate or reach its endpoint", err=True)
            elif isinstance(exc, OutpostRunnerError):
                typer.echo(exc.message, err=True)
            else:
                typer.echo("Outpost runner input or local state is invalid", err=True)
            raise typer.Exit(1) from exc

    @app.command("reconcile")
    def reconcile(
        run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
        api_url: Annotated[str, typer.Option(help="Original Outpost API base for this run.")],
    ) -> None:
        """Report delivery evidence from a stopped owner using its original parent token."""
        token = os.environ.get("OUTPOST_TOKEN", "")
        if not token:
            raise typer.BadParameter("OUTPOST_TOKEN must contain the original parent token")
        try:
            count = asyncio.run(reconcile_run(run_dir, api_url, token))
        except (OutpostRunnerError, ValueError, OSError, httpx.HTTPError) as exc:
            message = (
                exc.message
                if isinstance(exc, OutpostRunnerError)
                else "Outpost recovery could not validate or report the saved run"
            )
            typer.echo(message, err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"reported {count} delivery outcomes")

    @app.command("pause")
    def pause(run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
        """Hold new wake submissions for this explicit owner directory."""
        try:
            set_run_paused(run_dir, True)
        except OutpostRunnerError as exc:
            raise typer.BadParameter(exc.message) from exc
        typer.echo("wake submissions paused")

    @app.command("resume")
    def resume(run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
        """Allow held wake submissions to the existing owner."""
        try:
            set_run_paused(run_dir, False)
        except OutpostRunnerError as exc:
            raise typer.BadParameter(exc.message) from exc
        typer.echo("wake submissions resumed")

    return app
