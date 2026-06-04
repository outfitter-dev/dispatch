"""Surface parity (the no-drift gate, ADR-0000): per op, the CLI options, the MCP
inputSchema, and the input model agree; annotations agree with intent/idempotent;
and the error taxonomy projects from one table. Behavior, not just op names."""

from __future__ import annotations

import inspect

import typer

from outfitter.dispatch.contracts.derive_cli import derive_cli
from outfitter.dispatch.contracts.derive_mcp import derive_mcp_projection
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    AuthorityError,
    DispatchError,
    LaneBusyError,
    NotFoundError,
    ValidationError,
    project_error,
)
from outfitter.dispatch.core.ops import REGISTRY


def _stub_invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
    return {}


def _cli_param_names(app: typer.Typer, op_id: str) -> set[str]:
    for command in app.registered_commands:
        if command.name == op_id and command.callback is not None:
            return set(inspect.signature(command.callback).parameters)
    raise AssertionError(f"no CLI command for op {op_id!r}")


def test_cli_mcp_model_parity_per_op() -> None:
    app = derive_cli(REGISTRY, _stub_invoke)
    projection = derive_mcp_projection(REGISTRY)
    routes_by_op = {route.op.id: route for route in projection.routes.values()}
    mcp_tools = {t.name: t for t in projection.tools}
    assert set(routes_by_op) == set(REGISTRY.ids())

    for op in REGISTRY:
        fields = set(op.input.model_fields)
        # CLI options ↔ input model
        assert _cli_param_names(app, op.id) == fields, f"{op.id} CLI options"
        route = routes_by_op[op.id]
        tool = mcp_tools[route.tool_name]
        variants = tool.inputSchema["oneOf"]
        schema = next(
            variant for variant in variants if variant["properties"]["op"]["const"] == route.action
        )
        # MCP inputSchema ↔ input model
        assert set(schema.get("properties", {})) == fields | {"op"}, f"{op.id} MCP inputSchema"
        assert tool.outputSchema is not None, f"{op.id} missing outputSchema"
        assert op.output.model_json_schema() in tool.outputSchema["oneOf"], f"{op.id} outputSchema"
        # Tool-level safety annotations are exact because groups do not mix intents.
        ann = tool.annotations
        assert ann is not None
        assert ann.readOnlyHint == (op.intent == "read"), op.id
        assert ann.destructiveHint == (op.intent == "destroy"), op.id


def test_error_taxonomy_projects_from_one_table() -> None:
    cases: list[tuple[DispatchError, int, int]] = [
        (NotFoundError("x"), 4, 1004),
        (ValidationError("x"), 2, 1002),
        (LaneBusyError("x"), 5, 1005),
        (AuthorityError("x"), 7, 1007),
        (AppServerError("x"), 8, 1008),
    ]
    for exc, exit_code, rpc_code in cases:
        proj = project_error(exc)
        assert proj.exit_code == exit_code  # CLI projection
        assert proj.rpc_code == rpc_code  # control-socket / MCP projection
        assert proj.code == exc.code
