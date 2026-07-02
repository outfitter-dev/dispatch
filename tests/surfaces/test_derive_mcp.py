"""MCP tool derivation from the op registry."""

from __future__ import annotations

from outfitter.dispatch.contracts.derive_mcp import derive_mcp_projection
from outfitter.dispatch.core.ops import REGISTRY


def test_grouped_tools_are_agent_oriented_not_one_per_op() -> None:
    projection = derive_mcp_projection(REGISTRY)
    assert [t.name for t in projection.tools] == [
        "dispatch_thread_read",
        "dispatch_inbox_read",
        "dispatch_inbox_write",
        "dispatch_inbox_destroy",
        "dispatch_thread_write",
        "dispatch_thread_destroy",
        "dispatch_trigger_read",
        "dispatch_trigger_write",
        "dispatch_trigger_destroy",
        "dispatch_daemon_read",
    ]
    assert len(projection.tools) < len(REGISTRY.ids())


def test_routes_cover_every_registry_op_once() -> None:
    projection = derive_mcp_projection(REGISTRY)
    routed = [route.op.id for route in projection.routes.values()]
    assert sorted(routed) == sorted(REGISTRY.ids())
    assert len(routed) == len(set(routed))


def test_action_schema_and_annotations_from_op() -> None:
    projection = derive_mcp_projection(REGISTRY)
    tools = {t.name: t for t in projection.tools}

    lane_write = tools["dispatch_thread_write"]
    assert lane_write.annotations is not None
    assert lane_write.annotations.readOnlyHint is False
    assert lane_write.annotations.destructiveHint is False
    assert lane_write.annotations.idempotentHint is False
    one_of = lane_write.inputSchema["oneOf"]
    new_schema = next(s for s in one_of if s["properties"]["op"]["const"] == "new")
    send_schema = next(s for s in one_of if s["properties"]["op"]["const"] == "send")
    assert set(new_schema["properties"]) >= {"op", "name", "preset", "goal", "text", "send"}
    assert "caller_thread_id" not in send_schema["properties"]
    assert {s["properties"]["op"]["const"] for s in one_of} >= {
        "fork",
        "goal_set",
        "compact",
        "restore",
    }

    lane_read = tools["dispatch_thread_read"]
    assert lane_read.annotations is not None
    assert lane_read.annotations.readOnlyHint is True
    assert lane_read.annotations.idempotentHint is True
    search_schema = next(
        s for s in lane_read.inputSchema["oneOf"] if s["properties"]["op"]["const"] == "search"
    )
    query_schema = next(
        s for s in lane_read.inputSchema["oneOf"] if s["properties"]["op"]["const"] == "query"
    )
    assert "local" not in search_schema["properties"]
    assert {"tool", "file", "arg_key"} <= set(query_schema["properties"])
    assert {s["properties"]["op"]["const"] for s in lane_read.inputSchema["oneOf"]} >= {
        "transcript",
        "watch",
        "goal_get",
        "search",
        "query",
    }

    lane_destroy = tools["dispatch_thread_destroy"]
    assert lane_destroy.annotations is not None
    assert lane_destroy.annotations.destructiveHint is True
    assert {s["properties"]["op"]["const"] for s in lane_destroy.inputSchema["oneOf"]} >= {
        "archive",
        "rollback",
        "goal_clear",
    }

    daemon_read = tools["dispatch_daemon_read"]
    assert daemon_read.annotations is not None
    assert daemon_read.annotations.readOnlyHint is True
    assert {s["properties"]["op"]["const"] for s in daemon_read.inputSchema["oneOf"]} >= {
        "status",
        "log",
        "models",
    }
