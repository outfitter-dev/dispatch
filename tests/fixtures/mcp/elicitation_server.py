"""Tiny stdio MCP server whose one tool requests a structured elicitation."""

from __future__ import annotations

import json
import sys

ELICITATION_ID = "dispatch-elicit-1"
pending_tool_call_id: object | None = None


def send(message: dict[str, object]) -> None:
    print(json.dumps(message, separators=(",", ":")), flush=True)


for line in sys.stdin:
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        continue
    if not isinstance(message, dict):
        continue
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        params = message.get("params")
        protocol = params.get("protocolVersion") if isinstance(params, dict) else None
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": protocol or "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dispatch-elicitation-probe", "version": "1"},
                },
            }
        )
    elif method == "tools/list":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "ask_color",
                            "description": "Ask the operator to choose red or blue.",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        pending_tool_call_id = request_id
        send(
            {
                "jsonrpc": "2.0",
                "id": ELICITATION_ID,
                "method": "elicitation/create",
                "params": {
                    "message": "Choose a color",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"color": {"type": "string", "enum": ["red", "blue"]}},
                        "required": ["color"],
                    },
                },
            }
        )
    elif request_id == ELICITATION_ID and pending_tool_call_id is not None:
        result = message.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        color = content.get("color") if isinstance(content, dict) else "none"
        send(
            {
                "jsonrpc": "2.0",
                "id": pending_tool_call_id,
                "result": {
                    "content": [{"type": "text", "text": f"selected {color}"}],
                    "isError": False,
                },
            }
        )
        pending_tool_call_id = None
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
