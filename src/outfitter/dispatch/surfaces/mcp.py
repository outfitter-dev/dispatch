"""MCP surface: a stdio MCP server derived from the same op registry.

Tool calls route to the daemon over the control socket (same as the CLI) — no
per-op MCP code. Errors project to ``isError`` + ``_meta`` (the DispatchError
taxonomy, ADR-0001). The daemon is the single executor (ADR-0002/0009).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from outfitter.dispatch import config
from outfitter.dispatch.contracts.derive_mcp import McpProjection, derive_mcp_projection


def _io_error(message: str) -> dict[str, object]:
    return {"error": {"code": -32603, "message": message, "data": {}}}


async def call_daemon(
    socket_path: Path, method: str, params: dict[str, object], timeout: float = 30.0
) -> dict[str, object]:
    """Send one control request to the daemon and return the raw response.

    Never hangs (bounded by ``timeout``) and never leaks the connection (ADR-0009):
    failures return a structured error instead of raising.
    """
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
    except OSError as exc:
        return _io_error(f"daemon unreachable: {exc}")
    try:
        writer.write((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode())
        await asyncio.wait_for(writer.drain(), timeout)
        line = await asyncio.wait_for(reader.readline(), timeout)
        if not line:
            return _io_error("no response from daemon")
        try:
            parsed: dict[str, object] = json.loads(line)
        except json.JSONDecodeError:
            return _io_error("malformed response from daemon")
        return parsed
    except (TimeoutError, OSError) as exc:
        return _io_error(f"daemon I/O failed: {exc}")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def handle_tool_call(
    socket_path: Path,
    name: str,
    arguments: dict[str, object],
    projection: McpProjection | None = None,
) -> CallToolResult:
    if projection is None:
        from outfitter.dispatch.core.ops import REGISTRY

        projection = derive_mcp_projection(REGISTRY)
    route = _route_tool_call(projection, name, arguments)
    if isinstance(route, CallToolResult):
        return route
    method, params = route
    response = await call_daemon(socket_path, method, params)
    error = response.get("error")
    if isinstance(error, dict):
        data = error.get("data")
        meta = data if isinstance(data, dict) else {}
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=str(error.get("message")))],
            _meta={"code": error.get("code"), **meta},
        )
    result = response.get("result")
    structured = result if isinstance(result, dict) else {}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(structured))],
        structuredContent=structured,
    )


def build_server(socket_path: Path) -> Server[object, object]:
    from outfitter.dispatch.core.ops import REGISTRY

    server: Server[object, object] = Server("dispatch")
    projection = derive_mcp_projection(REGISTRY)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return projection.tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, object]) -> CallToolResult:
        return await handle_tool_call(socket_path, name, arguments, projection)

    return server


async def _serve(socket_path: Path) -> None:
    server = build_server(socket_path)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_mcp(socket_path: Path | None = None) -> None:
    """`dispatch mcp` entrypoint: serve MCP tools over stdio."""
    asyncio.run(_serve(socket_path if socket_path is not None else config.socket_path()))


def _route_tool_call(
    projection: McpProjection, tool_name: str, arguments: dict[str, object]
) -> tuple[str, dict[str, object]] | CallToolResult:
    action = arguments.get("op")
    if not isinstance(action, str):
        return _tool_error("missing string op", code=-32602)
    route = projection.routes.get((tool_name, action))
    if route is None:
        return _tool_error(f"unknown dispatch MCP action {tool_name}/{action}", code=-32601)
    params = dict(arguments)
    del params["op"]
    if route.op.id == "send" and params.get("intro") is True:
        params["caller_thread_id"] = os.environ.get("CODEX_THREAD_ID")
    return route.op.id, params


def _tool_error(message: str, *, code: int) -> CallToolResult:
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=message)],
        _meta={"code": code, "dispatchCode": "mcp_route_error", "exitCode": 2},
    )
