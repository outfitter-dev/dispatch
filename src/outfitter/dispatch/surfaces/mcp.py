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
from outfitter.dispatch.contracts.errors import DaemonStaleError, project_error
from outfitter.dispatch.contracts.registry import (
    CONTROL_EXEC_METHOD,
    CONTROL_META_METHOD,
    ControlOpCompatibility,
    control_op_compatibility,
)

_METHOD_NOT_FOUND = -32601


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
        return await _stream_request(reader, writer, method, params, timeout)
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
    from outfitter.dispatch.contracts.registry import (
        op_schema_hash,
        registry_legacy_safe_ops,
        registry_read_safe_ops,
    )
    from outfitter.dispatch.core.ops import REGISTRY

    if projection is None:
        projection = derive_mcp_projection(REGISTRY)
    route = _route_tool_call(projection, name, arguments)
    if isinstance(route, CallToolResult):
        return route
    method, params = route
    # Never forward op input to a daemon whose schema for THIS op differs from
    # this process's: a stale daemon parses input with Pydantic's default
    # ``extra="ignore"`` and would silently drop fields it does not know. Ops
    # whose schemas match stay usable even when other ops drifted.
    op = REGISTRY.get(method)
    compatibility = await _daemon_op_compatibility(
        socket_path,
        op.id,
        op_schema_hash(op),
        read_safe=op.id in registry_read_safe_ops(REGISTRY),
        baseline_safe=op.id in registry_legacy_safe_ops(REGISTRY),
    )
    skew = _compatibility_problem(compatibility, op.id)
    if skew is not None:
        return _stale_tool_error(skew)
    if compatibility.mode == "legacy":
        response, compatibility = await _call_daemon_bound(
            socket_path,
            method,
            params,
            op_schema_hash(op),
            read_safe=op.id in registry_read_safe_ops(REGISTRY),
            baseline_safe=op.id in registry_legacy_safe_ops(REGISTRY),
        )
        skew = _compatibility_problem(compatibility, op.id)
        if skew is not None:
            return _stale_tool_error(skew)
    else:
        response = await call_daemon(
            socket_path,
            CONTROL_EXEC_METHOD,
            {"op": method, "params": params, "op_schema_hash": op_schema_hash(op)},
        )
    error = response.get("error")
    if isinstance(error, dict):
        if compatibility.mode == "checked" and error.get("code") == _METHOD_NOT_FOUND:
            return _stale_tool_error(
                f"dispatch daemon does not support checked execution for op {op.id!r}"
            )
        data = error.get("data")
        if isinstance(data, dict) and data.get("dispatchCode") == DaemonStaleError.code:
            return _stale_tool_error(str(error.get("message")))
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


async def _daemon_op_compatibility(
    socket_path: Path,
    op_id: str,
    expected_hash: str,
    *,
    read_safe: bool,
    baseline_safe: bool,
) -> ControlOpCompatibility:
    """Read metadata and choose the allowed execution mode for one op.

    The shared contract policy classifies the response as checked, proven
    legacy, or blocked; this async function owns only the metadata transport.
    """
    response = await call_daemon(socket_path, CONTROL_META_METHOD, {})
    return control_op_compatibility(
        response,
        op_id,
        expected_hash,
        read_safe=read_safe,
        baseline_safe=baseline_safe,
    )


async def _call_daemon_bound(
    socket_path: Path,
    op_id: str,
    params: dict[str, object],
    expected_hash: str,
    *,
    read_safe: bool,
    baseline_safe: bool,
    timeout: float = 30.0,
) -> tuple[dict[str, object], ControlOpCompatibility]:
    """Repeat legacy admission and execute on the same established socket."""
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
    except OSError as exc:
        return _io_error(f"daemon unreachable: {exc}"), ControlOpCompatibility("checked")
    try:
        metadata = await _stream_request(reader, writer, CONTROL_META_METHOD, {}, timeout)
        compatibility = control_op_compatibility(
            metadata,
            op_id,
            expected_hash,
            read_safe=read_safe,
            baseline_safe=baseline_safe,
        )
        if compatibility.mode == "blocked":
            return {}, compatibility
        if compatibility.mode == "checked":
            checked_params: dict[str, object] = {
                "op": op_id,
                "params": params,
                "op_schema_hash": expected_hash,
            }
            response = await _stream_request(
                reader, writer, CONTROL_EXEC_METHOD, checked_params, timeout
            )
        else:
            response = await _stream_request(reader, writer, op_id, params, timeout)
        return response, compatibility
    except (TimeoutError, OSError) as exc:
        return _io_error(f"daemon I/O failed: {exc}"), ControlOpCompatibility("checked")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _stream_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    method: str,
    params: dict[str, object],
    timeout: float,
) -> dict[str, object]:
    writer.write((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode())
    await asyncio.wait_for(writer.drain(), timeout)
    line = await asyncio.wait_for(reader.readline(), timeout)
    if not line:
        return _io_error("no response from daemon")
    try:
        parsed: object = json.loads(line)
    except json.JSONDecodeError:
        return _io_error("malformed response from daemon")
    return parsed if isinstance(parsed, dict) else _io_error("malformed response from daemon")


def _compatibility_problem(compatibility: ControlOpCompatibility, op_id: str) -> str | None:
    if compatibility.mode != "blocked":
        return None
    if compatibility.reason == "hash_mismatch":
        return f"dispatch daemon op schemas do not match this MCP server for op {op_id!r}"
    reported = compatibility.reported_version
    version = f"version {reported}" if isinstance(reported, str) else "unreported version"
    if compatibility.reason == "checked_unavailable":
        return f"dispatch daemon lacks checked execution required for op {op_id!r} ({version})"
    return f"dispatch daemon predates the op-schema handshake ({version})"


def _stale_tool_error(message: str) -> CallToolResult:
    projection = project_error(DaemonStaleError(message))
    return CallToolResult(
        isError=True,
        content=[
            TextContent(
                type="text",
                text=f"{message}; restart it (`dispatch down && dispatch up`), then retry.",
            )
        ],
        _meta={
            "code": projection.rpc_code,
            "dispatchCode": projection.code,
            "exitCode": projection.exit_code,
        },
    )


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
