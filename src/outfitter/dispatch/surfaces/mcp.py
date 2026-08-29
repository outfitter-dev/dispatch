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
from outfitter.dispatch.contracts.registry import CONTROL_META_METHOD

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
    skew = await _daemon_op_skew(
        socket_path,
        op.id,
        op_schema_hash(op),
        read_safe=op.id in registry_read_safe_ops(REGISTRY),
        baseline_safe=op.id in registry_legacy_safe_ops(REGISTRY),
    )
    if skew is not None:
        return CallToolResult(
            isError=True,
            content=[
                TextContent(
                    type="text",
                    text=f"{skew}; restart it (`dispatch down && dispatch up`), then retry.",
                )
            ],
            _meta={"code": -32601, "dispatchCode": "daemon_stale", "exitCode": 8},
        )
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


async def _daemon_op_skew(
    socket_path: Path,
    op_id: str,
    expected_hash: str,
    *,
    read_safe: bool,
    baseline_safe: bool,
) -> str | None:
    """Describe daemon/MCP schema skew for ONE op, or ``None`` when safe to send.

    Mirrors the CLI pre-flight: only this op's fingerprint gates the call, so
    hash-matching ops stay usable against a busy stale daemon. A daemon that
    predates the handshake reports no hashes; ``read_safe`` ops pass when it
    self-reports at least the read baseline floor, ``baseline_safe`` ops
    (schema unchanged since the parent release, such as ``stop``) only when
    it self-reports exactly the parent version, and a daemon reporting no
    version at all (no metadata method, <= 0.8.1) gets nothing (see
    :func:`prehandshake_op_allowed`). Everything else — notably the ops
    whose schema drifted, like ``new``/``new-plan`` with ``provider`` — is
    treated as skewed. Any other probe failure also returns ``None`` — the op
    call that follows surfaces it with the normal projection.
    """
    from outfitter.dispatch.contracts.registry import prehandshake_op_allowed

    response = await call_daemon(socket_path, CONTROL_META_METHOD, {})
    error = response.get("error")
    if isinstance(error, dict):
        if error.get("code") == _METHOD_NOT_FOUND and not prehandshake_op_allowed(
            None, read_safe=read_safe, baseline_safe=baseline_safe
        ):
            return "dispatch daemon predates the op-schema handshake (older than this MCP server)"
        return None
    result = response.get("result")
    op_schemas = result.get("op_schemas") if isinstance(result, dict) else None
    if not isinstance(op_schemas, dict):
        reported = result.get("version") if isinstance(result, dict) else None
        if prehandshake_op_allowed(reported, read_safe=read_safe, baseline_safe=baseline_safe):
            return None
        version = f"version {reported}" if isinstance(reported, str) else "unreported version"
        return (
            f"dispatch daemon predates the op-schema handshake "
            f"({version}, older than this MCP server)"
        )
    if op_schemas.get(op_id) == expected_hash:
        return None
    return (
        f"dispatch daemon op schemas do not match this MCP server for op {op_id!r} (stale daemon)"
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
