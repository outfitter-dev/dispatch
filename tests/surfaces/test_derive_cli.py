"""Tests for the CLI derivation: option mapping, rendering, destroy-confirm, and
error exit codes — with a stub invoker (no daemon)."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from outfitter.dispatch.contracts.derive_cli import derive_cli
from outfitter.dispatch.core.ops import REGISTRY

runner = CliRunner()


def test_open_command_maps_options_to_params() -> None:
    captured: dict[str, object] = {}

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        captured["op"] = op_id
        captured["params"] = params
        return {"id": "L1", "handle": "@x", "source": "own", "status": "idle"}

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(app, ["open", "--name", "x", "--cwd", "/w"])
    assert result.exit_code == 0
    assert captured["op"] == "open"
    assert captured["params"] == {"name": "x", "cwd": "/w"}


def test_new_command_maps_repeated_presets_and_no_send() -> None:
    captured: dict[str, object] = {}

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        captured["op"] = op_id
        captured["params"] = params
        return {"id": "L1", "handle": "@x", "source": "own", "status": "idle", "sent": False}

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(
        app, ["new", "--name", "x", "--preset", "builder", "--preset", "fast", "--no-send"]
    )
    assert result.exit_code == 0
    assert captured["op"] == "new"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["preset"] == ["builder", "fast"]
    assert params["send"] is False


def test_destroy_op_prompts_for_confirmation() -> None:
    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        return {"id": "L1", "handle": "@x", "source": "own", "status": "archived"}

    app = derive_cli(REGISTRY, invoke)
    declined = runner.invoke(app, ["archive", "--lane", "L1"], input="n\n")
    assert declined.exit_code != 0  # aborted at the confirm
    confirmed = runner.invoke(app, ["archive", "--lane", "L1"], input="y\n")
    assert confirmed.exit_code == 0


def test_error_exit_code_propagates() -> None:
    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        raise typer.Exit(code=4)

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(app, ["show", "--lane", "ghost"])
    assert result.exit_code == 4
