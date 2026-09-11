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
from outfitter.dispatch.contracts.errors import (
    DaemonStaleError,
    ValidationError,
    project_error,
)
from outfitter.dispatch.contracts.execute import execute
from outfitter.dispatch.contracts.registry import (
    CONTROL_EXEC_METHOD,
    CONTROL_META_METHOD,
    OpRegistry,
    op_schema_hash,
    registry_op_schema_hashes,
    registry_schema_hash,
)
from outfitter.dispatch.version import package_version

_METHOD_NOT_FOUND = -32601
_INVALID_REQUEST = -32600
_CONTROL_PROTOCOL_VERSION = 2


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
        if method == CONTROL_META_METHOD:
            return {
                "id": mid,
                "result": {
                    "protocol_version": _CONTROL_PROTOCOL_VERSION,
                    "version": package_version(),
                    "supported_ops": self._registry.ids(),
                    "registry_hash": registry_schema_hash(self._registry),
                    "op_schemas": registry_op_schema_hashes(self._registry),
                },
            }
        raw_params = message.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        checked_hash: str | None = None
        if method == CONTROL_EXEC_METHOD:
            try:
                method, params, checked_hash = _checked_execution(params)
            except ValidationError as exc:
                return _projected_error(mid, exc)
        try:
            op = self._registry.get(method)
        except KeyError:
            if checked_hash is not None:
                return _projected_error(
                    mid,
                    DaemonStaleError(f"daemon does not support the caller's checked op {method!r}"),
                )
            return _error(mid, _METHOD_NOT_FOUND, f"unknown op {method!r}")
        try:
            if checked_hash is not None and checked_hash != op_schema_hash(op):
                raise DaemonStaleError(
                    f"daemon op schema does not match the caller for op {op.id!r}"
                )
            result = await execute(op, params, self._ctx)
        except Exception as exc:  # surface boundary: project every error (ADR-0001)
            return _projected_error(mid, exc)
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


def _checked_execution(
    params: dict[str, object],
) -> tuple[str, dict[str, object], str]:
    if set(params) != {"op", "params", "op_schema_hash"}:
        raise ValidationError("invalid checked execution envelope")
    op_id = params["op"]
    op_params = params["params"]
    schema_hash = params["op_schema_hash"]
    if (
        not isinstance(op_id, str)
        or not isinstance(op_params, dict)
        or not isinstance(schema_hash, str)
    ):
        raise ValidationError("invalid checked execution envelope")
    return op_id, op_params, schema_hash


def _projected_error(mid: object, exc: BaseException) -> dict[str, object]:
    proj = project_error(exc)
    return _error(
        mid,
        proj.rpc_code,
        proj.message,
        data={"dispatchCode": proj.code, "exitCode": proj.exit_code},
    )
