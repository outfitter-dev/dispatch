"""Derive grouped MCP tools from the op registry (ADR-0010).

The op registry remains the semantic source of truth. MCP is an ergonomic
projection: related ops are grouped into fewer tools, while action schemas,
outputs, safety annotations, and routing still derive from the underlying ops.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from mcp.types import Tool, ToolAnnotations

from .op import Intent, Op
from .registry import OpRegistry
from .schema import public_schema

Safety = Literal["read", "write", "destroy"]


@dataclass(frozen=True)
class McpActionRoute:
    tool_name: str
    action: str
    op: Op


@dataclass(frozen=True)
class McpProjection:
    tools: list[Tool]
    routes: dict[tuple[str, str], McpActionRoute]


@dataclass(frozen=True)
class _ToolGroup:
    name: str
    summary: str
    intent: Intent
    actions: tuple[tuple[str, str], ...]  # (action, op_id)


_GROUPS: tuple[_ToolGroup, ...] = (
    _ToolGroup(
        name="dispatch_thread_read",
        summary="Read managed and unmanaged thread state.",
        intent="read",
        actions=(
            ("roster", "roster"),
            ("discover", "discover"),
            ("show", "show"),
            ("transcript", "transcript"),
            ("watch", "watch"),
            ("goal_get", "goal-get"),
            ("search", "search"),
        ),
    ),
    _ToolGroup(
        name="dispatch_thread_write",
        summary="Create, attach, sync, rename, and send to managed threads.",
        intent="write",
        actions=(
            ("open", "open"),
            ("new", "new"),
            ("attach", "attach"),
            ("sync", "sync"),
            ("rename", "lane-rename"),
            ("send", "send"),
            ("stop", "stop"),
            ("restore", "restore"),
            ("goal_set", "goal-set"),
            ("fork", "fork"),
            ("compact", "compact"),
        ),
    ),
    _ToolGroup(
        name="dispatch_thread_destroy",
        summary="Archive or destroy managed thread state.",
        intent="destroy",
        actions=(("archive", "archive"), ("goal_clear", "goal-clear"), ("rollback", "rollback")),
    ),
    _ToolGroup(
        name="dispatch_trigger_read",
        summary="Read trigger state.",
        intent="read",
        actions=(("list", "trigger-list"),),
    ),
    _ToolGroup(
        name="dispatch_trigger_write",
        summary="Create or modify triggers.",
        intent="write",
        actions=(
            ("add", "trigger-add"),
            ("pause", "trigger-pause"),
            ("resume", "trigger-resume"),
        ),
    ),
    _ToolGroup(
        name="dispatch_trigger_destroy",
        summary="Remove triggers.",
        intent="destroy",
        actions=(("rm", "trigger-rm"),),
    ),
    _ToolGroup(
        name="dispatch_daemon_read",
        summary="Read daemon health and audit log state.",
        intent="read",
        actions=(("status", "status"), ("log", "log")),
    ),
)


def derive_mcp_projection(registry: OpRegistry) -> McpProjection:
    routes: dict[tuple[str, str], McpActionRoute] = {}
    tools: list[Tool] = []
    seen_ops: set[str] = set()
    for group in _GROUPS:
        ops = tuple((action, registry.get(op_id)) for action, op_id in group.actions)
        for action, op in ops:
            routes[(group.name, action)] = McpActionRoute(group.name, action, op)
            seen_ops.add(op.id)
        tools.append(_tool_for_group(group, ops))
    missing = set(registry.ids()) - seen_ops
    if missing:
        raise ValueError(f"MCP projection missing ops: {sorted(missing)}")
    return McpProjection(tools=tools, routes=routes)


def derive_mcp(registry: OpRegistry) -> list[Tool]:
    return derive_mcp_projection(registry).tools


def _tool_for_group(group: _ToolGroup, ops: tuple[tuple[str, Op], ...]) -> Tool:
    return Tool(
        name=group.name,
        description=_description(group, ops),
        inputSchema=_input_schema(ops),
        outputSchema=_output_schema(ops),
        annotations=ToolAnnotations(
            readOnlyHint=group.intent == "read",
            destructiveHint=group.intent == "destroy",
            idempotentHint=all(op.idempotent for _, op in ops),
        ),
    )


def _description(group: _ToolGroup, ops: tuple[tuple[str, Op], ...]) -> str:
    actions = "; ".join(f"`{action}`: {op.summary}" for action, op in ops)
    return f"{group.summary} Choose an op and provide its derived arguments. {actions}"


def _input_schema(ops: tuple[tuple[str, Op], ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": [action for action, _ in ops],
                "description": "Dispatch op to run within this grouped MCP tool.",
            }
        },
        "required": ["op"],
        "oneOf": [_action_input_schema(action, op) for action, op in ops],
        "discriminator": {"propertyName": "op"},
    }


def _action_input_schema(action: str, op: Op) -> dict[str, Any]:
    schema = public_schema(deepcopy(op.input.model_json_schema()))
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required")
    if not isinstance(required, list):
        required = []
    action_schema: dict[str, Any] = {
        "type": "object",
        "description": op.summary,
        "properties": {
            "op": {
                "type": "string",
                "const": action,
                "description": f"Routes to dispatch op `{op.id}`.",
            },
            **properties,
        },
        "required": ["op", *required],
        "additionalProperties": False,
    }
    return action_schema


def _output_schema(ops: tuple[tuple[str, Op], ...]) -> dict[str, Any]:
    return {"oneOf": [op.output.model_json_schema() for _, op in ops]}
