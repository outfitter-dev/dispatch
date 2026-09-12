"""Daemon host compatibility warnings."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from unittest.mock import ANY

import pytest
import structlog
from pytest import MonkeyPatch
from structlog.testing import capture_logs

from outfitter.dispatch.client.transport import StdioTransport, UnixSocketTransport
from outfitter.dispatch.daemon import host
from outfitter.dispatch.daemon.host import (
    _configured_transport,
    _spawn_client,
    _warn_if_codex_below_floor,
    run_daemon,
)
from outfitter.dispatch.daemon.provider_manager import SharedCoreFailure
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeSupervisedClient


async def _call(path: Path, method: str, params: dict[str, object]) -> dict[str, object]:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode())
    await writer.drain()
    response: dict[str, object] = json.loads(await reader.readline())
    writer.close()
    await writer.wait_closed()
    return response


def test_daemon_owns_stdio_by_default(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    monkeypatch.delenv("DISPATCH_APP_SERVER_SOCKET", raising=False)

    assert isinstance(_configured_transport(), StdioTransport)


def test_daemon_attaches_to_configured_socket(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    monkeypatch.setenv("DISPATCH_APP_SERVER_SOCKET", str(tmp_path / "app.sock"))

    assert isinstance(_configured_transport(), UnixSocketTransport)


async def test_daemon_warns_with_structured_below_floor_details(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\necho 'codex-cli 0.146.0'\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    with capture_logs() as logs:
        await _warn_if_codex_below_floor(structlog.get_logger())

    assert logs == [
        {
            "event": "dispatchd.codex_version_below_floor",
            "log_level": "warning",
            "minimum_version": "0.147.0",
            "path": str(binary),
            "version": "0.146.0",
        }
    ]


async def test_daemon_ignores_invalid_compatibility_manifest(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "codex"
    manifest = tmp_path / "protocol_manifest.json"
    binary.write_text("#!/bin/sh\necho 'codex-cli 0.147.0'\n")
    binary.chmod(0o755)
    manifest.write_text("{}")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr("outfitter.dispatch.codex_compat._manifest_path", lambda: manifest)

    with capture_logs() as logs:
        await _warn_if_codex_below_floor(structlog.get_logger())

    assert logs == []


async def test_failed_provider_initialization_closes_its_started_connection(
    monkeypatch: MonkeyPatch,
) -> None:
    class StartedTransport:
        started = False

        async def start(self) -> None:
            self.started = True

    transport = StartedTransport()
    client_closed = False

    class InitFailureClient:
        def __init__(self, _transport: object) -> None:
            assert _transport is transport

        async def start(self) -> None:
            return None

        async def initialize(self) -> None:
            raise RuntimeError("initialize failed")

        async def close(self) -> None:
            nonlocal client_closed
            client_closed = True

    monkeypatch.setattr(host, "_configured_transport", lambda: transport)
    monkeypatch.setattr(host, "AppServerClient", InitFailureClient)

    with pytest.raises(RuntimeError, match="initialize failed"):
        await _spawn_client()

    assert transport.started is True
    assert client_closed is True


async def test_control_and_cached_reads_start_while_codex_is_unavailable(
    monkeypatch: MonkeyPatch, socket_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "registry.db"
    socket_path = socket_dir / "dispatchd.sock"
    monkeypatch.delenv("DISPATCH_APP_SERVER_SOCKET", raising=False)
    store = await Registry.open(db_path)
    lane = await store.add_lane(id="cached-lane", handle="@cached", source="own", status="idle")
    receipt, _ = await store.reserve_delivery(
        key="cached-receipt",
        lane=lane.id,
        mode="send",
        payload='{"text":"persisted"}',
        text="persisted",
    )
    await store.close()
    attempts = 0

    async def unavailable_client() -> object:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Codex executable unavailable")

    async def skip_warning(_log: structlog.stdlib.BoundLogger) -> None:
        return None

    monkeypatch.setattr(host, "_spawn_client", unavailable_client)
    monkeypatch.setattr(host, "_warn_if_codex_below_floor", skip_warning)

    daemon = asyncio.create_task(run_daemon(socket_path, db_path))
    try:
        async with asyncio.timeout(1):
            while not socket_path.exists():
                await asyncio.sleep(0.01)
        async with asyncio.timeout(1):
            while attempts == 0:
                await asyncio.sleep(0.01)

        status = await _call(socket_path, "status", {})
        assert isinstance(status["result"], dict)
        assert status["result"]["lanes"] == 1
        assert status["result"]["idle"] == 1
        assert status["result"]["active"] == 0
        assert status["result"]["providers"] == [
            {
                "provider": "codex",
                "binding_id": "codex-default",
                "state": "unavailable",
                "reason": "Codex executable unavailable",
                "last_error": "Codex executable unavailable",
                "observed_at": ANY,
                "connection_generation": None,
                "owns_process": True,
            }
        ]
        fetched = await _call(socket_path, "delivery-get", {"receipt_id": receipt.id})
        assert isinstance(fetched["result"], dict)
        assert fetched["result"]["id"] == receipt.id
    finally:
        daemon.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await daemon

    assert not socket_path.exists()
    if daemon.done() and not daemon.cancelled():
        pytest.fail(f"daemon exited during provider outage: {daemon.exception()!r}")


async def test_shared_registry_recovery_failure_terminates_daemon(
    monkeypatch: MonkeyPatch, socket_dir: Path, tmp_path: Path
) -> None:
    socket_path = socket_dir / "dispatchd.sock"
    client = FakeSupervisedClient()

    async def spawn_client() -> FakeSupervisedClient:
        return client

    async def fail_recovery(_self: Registry) -> None:
        raise RuntimeError("registry unreadable")

    async def skip_warning(_log: structlog.stdlib.BoundLogger) -> None:
        return None

    monkeypatch.setattr(host, "_spawn_client", spawn_client)
    monkeypatch.setattr(host, "_warn_if_codex_below_floor", skip_warning)
    monkeypatch.setattr(Registry, "recover_deliveries", fail_recovery)

    with pytest.raises(SharedCoreFailure, match="shared registry delivery recovery failed"):
        await run_daemon(socket_path, tmp_path / "registry.db")

    assert client.closed.is_set()
    assert not socket_path.exists()
