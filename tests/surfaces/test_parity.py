"""Surface parity (the no-drift gate, ADR-0000/0010).

MCP remains exhaustive per op. The CLI is ergonomic and may group/compose ops, so
its gate checks representative routes project canonical op params rather than
requiring one top-level command per op.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from typer.testing import CliRunner

from outfitter.dispatch.contracts.derive_cli import (
    CLI_PROJECTION_CONTROL_PATHS,
    cli_public_routes,
    cli_schema_routes,
    derive_cli,
)
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
from outfitter.dispatch.contracts.schema import is_internal_field
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.surfaces.cli import CLI_SURFACE_CONTROL_PATHS, build_cli


def _stub_invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
    return {}


runner = CliRunner()


_EXPECTED_CLI_SCHEMA_ROUTES = {
    "send": "send",
    "stop": "stop",
    "new": "new",
    "new --dry-run": "new-plan",
    "attach": "attach",
    "list": "roster",
    "list --unmanaged": "discover",
    "get": "show",
    "tail": "transcript",
    "watch": "watch",
    "sync": "sync",
    "search": "search",
    "rename": "lane-rename",
    "archive": "archive",
    "restore": "restore",
    "models": "models",
    "goal status": "goal-get",
    "goal set": "goal-set",
    "goal clear": "goal-clear",
    "trigger add": "trigger-add",
    "trigger list": "trigger-list",
    "trigger rm": "trigger-rm",
    "trigger pause": "trigger-pause",
    "trigger resume": "trigger-resume",
    "daemon status": "status",
    "daemon log": "log",
}


def test_mcp_model_parity_per_op() -> None:
    projection = derive_mcp_projection(REGISTRY)
    routes_by_op = {route.op.id: route for route in projection.routes.values()}
    mcp_tools = {t.name: t for t in projection.tools}
    assert set(routes_by_op) == set(REGISTRY.ids())

    for op in REGISTRY:
        fields = {
            name for name, field in op.input.model_fields.items() if not is_internal_field(field)
        }
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


def test_cli_schema_routes_cover_public_ops() -> None:
    app = derive_cli(REGISTRY, _stub_invoke)

    routed_ops = set(cli_schema_routes().values())
    assert set(REGISTRY.ids()) - routed_ops == {"open", "fork", "rollback", "compact"}
    assert cli_schema_routes() == _EXPECTED_CLI_SCHEMA_ROUTES

    for command, op_id in cli_schema_routes().items():
        result = runner.invoke(app, ["schema", command])
        assert result.exit_code == 0, command
        assert json.loads(result.output)["op"] == op_id


def test_cli_registered_commands_are_declared_in_projection_manifest() -> None:
    app = derive_cli(REGISTRY, _stub_invoke)

    registered = _registered_paths(app)
    declared = {route.path for route in cli_public_routes()} | set(CLI_PROJECTION_CONTROL_PATHS)

    assert registered == declared


def test_full_cli_commands_are_declared_projection_or_control_paths() -> None:
    app = build_cli(socket_path=Path("/tmp/dispatch-test.sock"))

    registered = _registered_paths(app)
    declared = (
        {route.path for route in cli_public_routes()}
        | set(CLI_PROJECTION_CONTROL_PATHS)
        | set(CLI_SURFACE_CONTROL_PATHS)
    )

    assert registered == declared


def _registered_paths(app: typer.Typer) -> set[tuple[str, ...]]:
    root_paths = {
        (command.name,) for command in app.registered_commands if command.name is not None
    }
    group_paths: set[tuple[str, str]] = set()
    for group in app.registered_groups:
        if group.name is None or group.typer_instance is None:
            continue
        for command in group.typer_instance.registered_commands:
            if command.name is not None:
                group_paths.add((group.name, command.name))
    return root_paths | group_paths


def test_managed_thread_outputs_include_stable_identity_fields() -> None:
    app = derive_cli(REGISTRY, _stub_invoke)
    required = {"lane", "ref", "id", "title", "handle", "managed", "source", "status", "cwd"}

    for command in ("send", "goal status", "sync", "tail", "watch"):
        result = runner.invoke(app, ["schema", command])
        assert result.exit_code == 0, command
        output = json.loads(result.output)["output"]
        assert required <= set(output["properties"]), command


def test_cli_composed_routes_invoke_canonical_ops() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((op_id, params))
        if op_id in {"roster", "discover"}:
            return {"lanes": []} if op_id == "roster" else {"sessions": []}
        if op_id == "stop":
            return {"lane": "L1", "op": "stop", "accepted": True}
        if op_id == "send":
            return {"lane": "L1", "op": "send", "accepted": True}
        if op_id == "search":
            return {
                "query": "needle",
                "matches": [],
                "scanned": 0,
                "next_cursor": None,
                "experimental": True,
            }
        if op_id == "goal-get":
            return {"lane": "L1", "goal": None}
        return {}

    app = derive_cli(REGISTRY, invoke)

    assert runner.invoke(app, ["send", "@docs", "hi", "--context"]).exit_code == 0
    assert runner.invoke(app, ["stop", "@docs"]).exit_code == 0
    assert runner.invoke(app, ["list", "--unmanaged"]).exit_code == 0
    assert runner.invoke(app, ["sync", "@docs"]).exit_code == 0
    assert runner.invoke(app, ["search", "needle", "--managed"]).exit_code == 0
    assert runner.invoke(app, ["goal", "status", "@docs"]).exit_code == 0

    assert calls == [
        ("send", {"lane": "@docs", "text": "hi", "mode": "context", "intro": False}),
        ("stop", {"lane": "@docs"}),
        ("discover", {"limit": 50}),
        ("sync", {"lane": "@docs", "full": False}),
        (
            "search",
            {
                "query": "needle",
                "lane": None,
                "directory": None,
                "repo": None,
                "managed": True,
                "unmanaged": False,
                "archived": False,
                "since": None,
                "until": None,
                "date_field": "updated_at",
                "sort": "updated_at",
                "ascending": False,
                "limit": 20,
                "max_scan": 200,
            },
        ),
        ("goal-get", {"lane": "@docs"}),
    ]


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
