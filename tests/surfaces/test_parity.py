"""Surface parity (the no-drift gate, ADR-0000/0010).

MCP remains exhaustive per op. The CLI is ergonomic and may group/compose ops, so
its gate checks representative routes project canonical op params rather than
requiring one top-level command per op.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import typer
from typer.testing import CliRunner

from outfitter.dispatch.contracts.derive_cli import (
    CLI_ADAPTED_INPUT_FIELDS,
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
    "history": "history",
    "watch": "watch",
    "sync": "sync",
    "search": "search",
    "query": "query",
    "subscribe": "subscribe",
    "inbox list": "inbox-list",
    "inbox read": "inbox-read",
    "inbox ack": "inbox-ack",
    "request list": "server-request-list",
    "request respond": "server-request-respond",
    "subscriptions": "subscription-list",
    "unsubscribe": "unsubscribe",
    "rename": "lane-rename",
    "archive": "archive",
    "restore": "restore",
    "models": "models",
    "permissions": "permissions",
    "usage": "usage",
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


def test_usage_schema_is_jq_friendly_and_surface_derived() -> None:
    result = runner.invoke(derive_cli(REGISTRY, _stub_invoke), ["schema", "usage"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["op"] == "usage"
    observation = payload["output"]["$defs"]["UsageObservationView"]
    assert {
        "provider",
        "host",
        "state",
        "stale",
        "windows",
        "source",
        "observed_at",
        "confidence",
    } <= set(observation["properties"])


def test_cli_registered_commands_are_declared_in_projection_manifest() -> None:
    app = derive_cli(REGISTRY, _stub_invoke)

    registered = _registered_paths(app)
    declared = {route.path for route in cli_public_routes()} | set(CLI_PROJECTION_CONTROL_PATHS)

    assert registered == declared


def test_single_route_cli_adapters_cover_every_authored_input_field() -> None:
    app = derive_cli(REGISTRY, _stub_invoke)
    callbacks = {
        command.name: command.callback
        for command in app.registered_commands
        if command.name is not None and command.callback is not None
    }

    for route, op_id in cli_schema_routes().items():
        if " " in route or route not in callbacks:
            continue
        authored = {
            name
            for name, field in REGISTRY.get(op_id).input.model_fields.items()
            if not is_internal_field(field)
        }
        parameters = set(inspect.signature(callbacks[route]).parameters) - {"json"}
        adapters = CLI_ADAPTED_INPUT_FIELDS.get(route, {})
        projected = parameters | set(adapters)
        assert authored <= projected, f"{route} missing CLI fields: {sorted(authored - projected)}"
        for authored_field, cli_fields in adapters.items():
            assert cli_fields <= parameters, (
                f"{route} adapter for {authored_field} missing CLI fields: "
                f"{sorted(cli_fields - parameters)}"
            )


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
    required = {
        "lane",
        "ref",
        "id",
        "title",
        "handle",
        "managed",
        "source",
        "status",
        "cwd",
        "writable",
        "capabilities",
        "write_locked_reason",
    }

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
        if op_id == "query":
            return {
                "query": None,
                "matches": [],
                "scanned": 0,
                "experimental": False,
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
    assert runner.invoke(app, ["query", "--tool", "linear.save_issue"]).exit_code == 0
    assert runner.invoke(app, ["goal", "status", "@docs"]).exit_code == 0

    assert calls == [
        ("send", {"lane": "@docs", "text": "hi", "mode": "context", "intro": False}),
        ("stop", {"lane": "@docs"}),
        (
            "discover",
            {
                "limit": 50,
                "archived": False,
                "parent": None,
                "ancestor": None,
                "root": None,
                "topology_limit": 50,
            },
        ),
        (
            "sync",
            {
                "lane": "@docs",
                "full": False,
                "max_turns": 50,
                "max_items": 500,
                "max_bytes": 524288,
                "max_seconds": 5.0,
            },
        ),
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
        (
            "query",
            {
                "query": None,
                "lane": None,
                "directory": None,
                "repo": None,
                "source": None,
                "status": None,
                "archived": False,
                "since": None,
                "until": None,
                "date_field": "updated_at",
                "item_type": None,
                "role": None,
                "tool": "linear.save_issue",
                "tool_server": None,
                "tool_status": None,
                "errored": None,
                "file": None,
                "file_under": None,
                "ext": None,
                "mentions_thread": None,
                "turn": None,
                "item_id": None,
                "arg_key": None,
                "raw_retained": None,
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
