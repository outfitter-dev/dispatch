"""Unit tests for App Server transports."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, unix_serve

from outfitter.dispatch.client.errors import ProtocolError, TransportError
from outfitter.dispatch.client.transport import StdioTransport, UnixSocketTransport


@pytest.fixture
def short_socket_path() -> Iterator[Path]:
    """macOS Unix socket paths must stay below SUN_LEN."""
    with TemporaryDirectory(prefix="dispatch-ws-", dir="/tmp") as directory:
        yield Path(directory) / "app.sock"


async def test_stdio_transport_sets_reader_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class DummyProcess:
        returncode = None
        stdin = None
        stdout = None
        stderr = None

    async def fake_create_subprocess_exec(*argv: str, **kwargs: Any) -> DummyProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    transport = StdioTransport(argv=("codex", "app-server"), read_limit=123_456)
    await transport.start()

    assert captured["argv"] == ("codex", "app-server")
    assert captured["kwargs"]["limit"] == 123_456
    assert captured["kwargs"]["stdin"] is asyncio.subprocess.PIPE
    assert captured["kwargs"]["stdout"] is asyncio.subprocess.PIPE
    assert captured["kwargs"]["stderr"] is asyncio.subprocess.PIPE


def test_stdio_transport_accepts_sequence_argv() -> None:
    argv: Sequence[str] = ("codex", "app-server", "--listen", "stdio://")
    transport = StdioTransport(argv=argv)
    assert transport.returncode is None


async def test_stdio_transport_reads_large_jsonl_line() -> None:
    payload = {"id": 1, "result": {"text": "x" * 70_000}}
    script = (
        "import json, sys; "
        f"sys.stdout.write({json.dumps(json.dumps(payload))} + '\\n'); "
        "sys.stdout.flush()"
    )
    transport = StdioTransport(
        argv=(sys.executable, "-c", script),
        read_limit=128 * 1024,
    )
    await transport.start()
    try:
        assert await transport.receive() == payload
    finally:
        await transport.close()


async def test_unix_socket_transport_round_trips_json(short_socket_path: Path) -> None:
    async def echo(connection: ServerConnection) -> None:
        await connection.send(await connection.recv())

    async with unix_serve(echo, path=str(short_socket_path)):
        transport = UnixSocketTransport(short_socket_path)
        await transport.start()
        try:
            payload = {"id": 1, "method": "initialize", "params": {}}
            await transport.send(payload)
            assert await transport.receive() == payload
        finally:
            await transport.close()


async def test_unix_socket_transport_rejects_non_object_json(
    short_socket_path: Path,
) -> None:
    async def send_array(connection: ServerConnection) -> None:
        await connection.recv()
        await connection.send("[]")

    async with unix_serve(send_array, path=str(short_socket_path)):
        transport = UnixSocketTransport(short_socket_path)
        await transport.start()
        try:
            await transport.send({"id": 1})
            with pytest.raises(ProtocolError, match="expected a JSON object"):
                await transport.receive()
        finally:
            await transport.close()


async def test_unix_socket_transport_reports_missing_socket(tmp_path: Path) -> None:
    transport = UnixSocketTransport(tmp_path / "missing.sock")

    with pytest.raises(TransportError, match="failed to connect"):
        await transport.start()


async def test_unix_socket_transport_close_does_not_stop_server(
    short_socket_path: Path,
) -> None:
    async def echo(connection: ServerConnection) -> None:
        await connection.send(await connection.recv())

    async with unix_serve(echo, path=str(short_socket_path)):
        first = UnixSocketTransport(short_socket_path)
        await first.start()
        await first.close()

        second = UnixSocketTransport(short_socket_path)
        await second.start()
        try:
            await second.send({"id": 2})
            assert await second.receive() == {"id": 2}
        finally:
            await second.close()
