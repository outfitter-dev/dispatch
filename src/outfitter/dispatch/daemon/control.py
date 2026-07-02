"""The daemon control server — the canonical surface every other surface derives
from (ADR-0008: JSON-RPC-lite over newline-delimited JSON on a Unix socket).

Requests ``{id, method, params}`` map to ops; responses are ``{id, result}`` or
``{id, error:{code, message, data}}``. The ``DispatchError`` taxonomy projects
into the error shape here. (Server-push notifications for streaming surfaces land
in a later phase; v0 is request/response.)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import project_error
from outfitter.dispatch.contracts.execute import execute
from outfitter.dispatch.contracts.registry import OpRegistry
from outfitter.dispatch.version import package_version

_METHOD_NOT_FOUND = -32601
_INVALID_REQUEST = -32600
_CONTROL_META_METHOD = "__dispatch/metadata"
_CONTROL_PROTOCOL_VERSION = 1


class ControlServer:
    """Serves the op registry over a Unix socket."""

    def __init__(self, registry: OpRegistry, ctx: Ctx) -> None:
        self._registry = registry
        self._ctx = ctx
        self._server: asyncio.Server | None = None

    async def dispatch(self, message: dict[str, object]) -> dict[str, object]:
        mid = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(mid, _INVALID_REQUEST, "missing 'method'")
        if method == _CONTROL_META_METHOD:
            return {
                "id": mid,
                "result": {
                    "protocol_version": _CONTROL_PROTOCOL_VERSION,
                    "version": package_version(),
                    "supported_ops": self._registry.ids(),
                },
            }
        raw_params = message.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        try:
            op = self._registry.get(method)
        except KeyError:
            return _error(mid, _METHOD_NOT_FOUND, f"unknown op {method!r}")
        try:
            result = await execute(op, params, self._ctx)
        except Exception as exc:  # surface boundary: project every error (ADR-0001)
            proj = project_error(exc)
            return _error(
                mid,
                proj.rpc_code,
                proj.message,
                data={"dispatchCode": proj.code, "exitCode": proj.exit_code},
            )
        return {"id": mid, "result": result}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                response = await self.dispatch(parsed)
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def serve(self, path: str | Path) -> asyncio.Server:
        self._server = await asyncio.start_unix_server(self._handle, path=str(path))
        return self._server

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()


def _error(
    mid: object, code: int, message: str, data: dict[str, object] | None = None
) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"id": mid, "error": error}
