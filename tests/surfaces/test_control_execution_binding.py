"""Execution requests stay bound to the daemon connection that admitted them."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
import typer
from mcp.types import TextContent

from outfitter.dispatch.contracts.legacy_baseline import PARENT_VERSION
from outfitter.dispatch.contracts.registry import (
    CONTROL_EXEC_METHOD,
    CONTROL_META_METHOD,
    ControlOpCompatibility,
    OpRegistry,
    control_op_compatibility,
    registry_legacy_safe_ops,
    registry_op_schema_hashes,
    registry_read_safe_ops,
)
from outfitter.dispatch.core.models import RosterInput
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.daemon.control import ControlServer
from outfitter.dispatch.registry.store import Registry
from outfitter.dispatch.surfaces import cli, mcp
from tests.fakes import make_ctx


class _StaleRosterInput(RosterInput):
    stale: bool = False


class _CurrentMetadataServer(ControlServer):
    async def dispatch(self, message: dict[str, object]) -> dict[str, object]:
        if message.get("method") == CONTROL_META_METHOD:
            return {
                "id": message.get("id"),
                "result": {
                    "protocol_version": 2,
                    "version": "test",
                    "supported_ops": REGISTRY.ids(),
                    "op_schemas": registry_op_schema_hashes(REGISTRY),
                },
            }
        return await super().dispatch(message)


def test_protocol_v1_hash_match_only_allows_derived_legacy_safe_ops() -> None:
    metadata: dict[str, object] = {
        "result": {
            "protocol_version": 1,
            "version": PARENT_VERSION,
            "op_schemas": registry_op_schema_hashes(REGISTRY),
        }
    }

    safe = control_op_compatibility(
        metadata,
        "stop",
        registry_op_schema_hashes(REGISTRY)["stop"],
        read_safe=False,
        baseline_safe=True,
    )
    drift_sensitive = control_op_compatibility(
        metadata,
        "new-plan",
        registry_op_schema_hashes(REGISTRY)["new-plan"],
        read_safe=False,
        baseline_safe=False,
    )

    assert safe.mode == "legacy"
    assert drift_sensitive.mode == "blocked"
    assert drift_sensitive.reason == "checked_unavailable"


def _invoke_cli(path: Path, op_id: str, params: dict[str, object]) -> dict[str, object]:
    return cli.invoke_daemon(
        path,
        frozenset(REGISTRY.ids()),
        registry_read_safe_ops(REGISTRY),
        registry_legacy_safe_ops(REGISTRY),
        lambda: registry_op_schema_hashes(REGISTRY),
        op_id,
        params,
        retry_on_stale=False,
    )


def test_cli_legacy_metadata_and_raw_op_share_one_socket(socket_dir: Path) -> None:
    path = socket_dir / "legacy.sock"
    ready = threading.Event()
    observed: list[tuple[int, str]] = []

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen()
            listener.settimeout(3)
            ready.set()
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                stream = connection.makefile("rwb")
                for _ in range(2):
                    request = json.loads(stream.readline())
                    observed.append((id(connection), request["method"]))
                    result = (
                        {
                            "protocol_version": 1,
                            "version": PARENT_VERSION,
                            "supported_ops": ["archive"],
                            "op_schemas": {
                                "archive": registry_op_schema_hashes(REGISTRY)["archive"]
                            },
                        }
                        if request["method"] == CONTROL_META_METHOD
                        else {"ok": True}
                    )
                    stream.write((json.dumps({"id": 1, "result": result}) + "\n").encode())
                    stream.flush()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)

    assert _invoke_cli(path, "archive", {"target": "T1"}) == {"ok": True}
    thread.join(timeout=2)
    assert not thread.is_alive()

    assert observed == [
        (observed[0][0], CONTROL_META_METHOD),
        (observed[0][0], "archive"),
    ]


@pytest_asyncio.fixture
async def legacy_mcp_socket(socket_dir: Path) -> AsyncIterator[tuple[Path, list[tuple[int, str]]]]:
    path = socket_dir / "legacy-mcp.sock"
    observed: list[tuple[int, str]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = id(writer.transport)
        while line := await reader.readline():
            request: dict[str, object] = json.loads(line)
            method = request["method"]
            assert isinstance(method, str)
            observed.append((connection, method))
            result = (
                {
                    "protocol_version": 1,
                    "version": PARENT_VERSION,
                    "supported_ops": ["lane-rename"],
                    "op_schemas": {
                        "lane-rename": registry_op_schema_hashes(REGISTRY)["lane-rename"]
                    },
                }
                if method == CONTROL_META_METHOD
                else {"accepted": True}
            )
            writer.write((json.dumps({"id": 1, "result": result}) + "\n").encode())
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=str(path))
    try:
        yield path, observed
    finally:
        server.close()
        await server.wait_closed()


async def test_mcp_legacy_metadata_and_raw_op_share_one_socket(
    legacy_mcp_socket: tuple[Path, list[tuple[int, str]]],
) -> None:
    path, observed = legacy_mcp_socket

    result = await mcp.handle_tool_call(
        path,
        "dispatch_thread_write",
        {"op": "rename", "old": "T1", "new": "renamed"},
    )

    assert result.isError is False
    assert [method for _, method in observed] == [CONTROL_META_METHOD, "lane-rename"]
    assert observed[0][0] == observed[1][0]


async def test_mcp_checked_metadata_and_execution_share_one_socket(socket_dir: Path) -> None:
    path = socket_dir / "checked-mcp.sock"
    observed: list[tuple[int, str, dict[str, object]]] = []
    finished = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = id(writer.transport)
        try:
            for _ in range(2):
                request: dict[str, object] = json.loads(await reader.readline())
                method = request["method"]
                params = request["params"]
                assert isinstance(method, str)
                assert isinstance(params, dict)
                observed.append((connection, method, params))
                result = (
                    {
                        "protocol_version": 2,
                        "op_schemas": registry_op_schema_hashes(REGISTRY),
                    }
                    if method == CONTROL_META_METHOD
                    else {"lanes": []}
                )
                writer.write((json.dumps({"id": 1, "result": result}) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_unix_server(handle, path=str(path))
    try:
        result = await mcp.handle_tool_call(path, "dispatch_thread_read", {"op": "roster"})
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        server.close()
        await server.wait_closed()

    assert result.isError is False
    assert [method for _, method, _ in observed] == [CONTROL_META_METHOD, CONTROL_EXEC_METHOD]
    assert observed[0][0] == observed[1][0]
    assert observed[1][2] == {
        "op": "roster",
        "params": {},
        "op_schema_hash": registry_op_schema_hashes(REGISTRY)["roster"],
    }


async def test_mcp_blocked_compatibility_closes_without_execution(socket_dir: Path) -> None:
    path = socket_dir / "blocked-mcp.sock"
    observed: list[str] = []
    finished = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            metadata: dict[str, object] = json.loads(await reader.readline())
            observed.append(str(metadata["method"]))
            writer.write(
                (
                    json.dumps(
                        {
                            "id": 1,
                            "result": {
                                "protocol_version": 2,
                                "op_schemas": {
                                    **registry_op_schema_hashes(REGISTRY),
                                    "roster": "stale",
                                },
                            },
                        }
                    )
                    + "\n"
                ).encode()
            )
            await writer.drain()
            extra = await reader.readline()
            if extra:
                request: dict[str, object] = json.loads(extra)
                observed.append(str(request["method"]))
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_unix_server(handle, path=str(path))
    try:
        result = await mcp.handle_tool_call(path, "dispatch_thread_read", {"op": "roster"})
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        server.close()
        await server.wait_closed()

    assert result.isError is True
    assert result.meta is not None
    assert result.meta["dispatchCode"] == "daemon_stale"
    assert observed == [CONTROL_META_METHOD]


def test_cli_old_daemon_after_modern_preflight_never_receives_raw_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    methods: list[str] = []

    def bound(
        _path: Path,
        _op_id: str,
        _expected_hash: str,
        _params: dict[str, object],
        *,
        read_safe: bool,
        baseline_safe: bool,
    ) -> tuple[dict[str, object], str | None, bool]:
        assert not read_safe
        assert not baseline_safe
        methods.append(CONTROL_EXEC_METHOD)
        return {"error": {"code": -32601, "message": "unknown op"}}, None, True

    monkeypatch.setattr(cli, "_control_request_bound", bound)

    with pytest.raises(typer.Exit) as exc:
        _invoke_cli(tmp_path / "replaced.sock", "new-plan", {"provider": "claude"})

    assert exc.value.exit_code == 8
    assert methods == [CONTROL_EXEC_METHOD]
    assert "checked execution" in capsys.readouterr().err


async def test_mcp_checked_execution_method_missing_is_typed_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    methods: list[str] = []

    async def bound(
        _path: Path,
        _op_id: str,
        _params: dict[str, object],
        _expected_hash: str,
        *,
        read_safe: bool,
        baseline_safe: bool,
        timeout: float = 30.0,
    ) -> tuple[dict[str, object], ControlOpCompatibility]:
        del read_safe, baseline_safe, timeout
        methods.append(CONTROL_EXEC_METHOD)
        return (
            {"error": {"code": -32601, "message": "unknown op"}},
            control_op_compatibility(
                {
                    "result": {
                        "protocol_version": 2,
                        "op_schemas": registry_op_schema_hashes(REGISTRY),
                    }
                },
                "new-plan",
                registry_op_schema_hashes(REGISTRY)["new-plan"],
                read_safe=False,
                baseline_safe=False,
            ),
        )

    monkeypatch.setattr(mcp, "_call_daemon_bound", bound)
    result = await mcp.handle_tool_call(
        Path("/replaced.sock"),
        "dispatch_thread_read",
        {"op": "new_plan", "provider": "claude"},
    )

    assert result.isError is True
    assert result.meta is not None
    assert result.meta["dispatchCode"] == "daemon_stale"
    assert result.meta["exitCode"] == 8
    assert methods == [CONTROL_EXEC_METHOD]


async def test_mcp_socket_replacement_with_old_daemon_rejects_checked_method(
    socket_dir: Path,
) -> None:
    path = socket_dir / "replaced-mcp.sock"
    methods: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        for _ in range(2):
            line = await reader.readline()
            request: dict[str, object] = json.loads(line)
            method = request["method"]
            assert isinstance(method, str)
            methods.append(method)
            if method == CONTROL_META_METHOD:
                response: dict[str, object] = {
                    "id": 1,
                    "result": {
                        "protocol_version": 2,
                        "op_schemas": registry_op_schema_hashes(REGISTRY),
                    },
                }
            else:
                response = {"id": 1, "error": {"code": -32601, "message": "unknown op"}}
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=str(path))
    try:
        result = await mcp.handle_tool_call(
            path, "dispatch_thread_read", {"op": "new_plan", "provider": "claude"}
        )
    finally:
        server.close()
        await server.wait_closed()

    assert result.isError is True
    assert result.meta is not None
    assert result.meta["dispatchCode"] == "daemon_stale"
    assert result.meta["exitCode"] == 8
    assert methods == [CONTROL_META_METHOD, CONTROL_EXEC_METHOD]


async def test_mcp_receiving_daemon_hash_mismatch_is_typed_and_actionable(
    socket_dir: Path,
) -> None:
    stale_registry = OpRegistry()
    for op in REGISTRY:
        stale_registry.register(replace(op, input=_StaleRosterInput) if op.id == "roster" else op)
    store = await Registry.open()
    server = _CurrentMetadataServer(stale_registry, make_ctx(store))
    path = socket_dir / "checked-mismatch.sock"
    await server.serve(path)
    try:
        result = await mcp.handle_tool_call(path, "dispatch_thread_read", {"op": "roster"})
    finally:
        await server.close()
        await store.close()

    assert result.isError is True
    assert result.meta is not None
    assert result.meta["dispatchCode"] == "daemon_stale"
    assert result.meta["exitCode"] == 8
    first = result.content[0]
    assert isinstance(first, TextContent)
    assert "dispatch down && dispatch up" in first.text


def test_cli_receiving_daemon_hash_mismatch_is_typed_and_actionable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def bound(
        _path: Path,
        _op_id: str,
        _expected_hash: str,
        _params: dict[str, object],
        *,
        read_safe: bool,
        baseline_safe: bool,
    ) -> tuple[dict[str, object], str | None, bool]:
        return (
            {
                "error": {
                    "code": 1012,
                    "message": "daemon op schema does not match",
                    "data": {"dispatchCode": "daemon_stale", "exitCode": 8},
                }
            },
            None,
            True,
        )

    monkeypatch.setattr(cli, "_control_request_bound", bound)

    with pytest.raises(typer.Exit) as exc:
        _invoke_cli(tmp_path / "mismatch.sock", "roster", {})

    assert exc.value.exit_code == 8
    assert "dispatch down && dispatch up" in capsys.readouterr().err


async def test_mcp_lost_checked_connection_is_not_retried_as_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    methods: list[str] = []

    async def bound(
        _path: Path,
        _op_id: str,
        _params: dict[str, object],
        _expected_hash: str,
        *,
        read_safe: bool,
        baseline_safe: bool,
        timeout: float = 30.0,
    ) -> tuple[dict[str, object], ControlOpCompatibility]:
        del read_safe, baseline_safe, timeout
        methods.append(CONTROL_EXEC_METHOD)
        return (
            {"error": {"code": -32603, "message": "no response from daemon", "data": {}}},
            control_op_compatibility(
                {
                    "result": {
                        "protocol_version": 2,
                        "op_schemas": registry_op_schema_hashes(REGISTRY),
                    }
                },
                "roster",
                registry_op_schema_hashes(REGISTRY)["roster"],
                read_safe=True,
                baseline_safe=True,
            ),
        )

    monkeypatch.setattr(mcp, "_call_daemon_bound", bound)
    result = await mcp.handle_tool_call(
        Path("/lost.sock"), "dispatch_thread_read", {"op": "roster"}
    )

    assert result.isError is True
    assert methods == [CONTROL_EXEC_METHOD]


def test_cli_lost_checked_connection_is_not_retried_as_raw(socket_dir: Path) -> None:
    path = socket_dir / "lost-cli.sock"
    ready = threading.Event()
    observed: list[str] = []

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen()
            listener.settimeout(3)
            ready.set()
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                stream = connection.makefile("rwb")
                metadata: dict[str, object] = json.loads(stream.readline())
                observed.append(str(metadata["method"]))
                stream.write(
                    (
                        json.dumps(
                            {
                                "id": 1,
                                "result": {
                                    "protocol_version": 2,
                                    "op_schemas": registry_op_schema_hashes(REGISTRY),
                                },
                            }
                        )
                        + "\n"
                    ).encode()
                )
                stream.flush()
                checked: dict[str, object] = json.loads(stream.readline())
                observed.append(str(checked["method"]))
                # Drop the connection without answering. The client must not
                # guess that raw replay is safe.

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)

    with pytest.raises(typer.Exit) as exc:
        _invoke_cli(path, "roster", {})
    thread.join(timeout=2)

    assert exc.value.exit_code == 1
    assert not thread.is_alive()
    assert observed == [CONTROL_META_METHOD, CONTROL_EXEC_METHOD]
