"""End-to-end over the real control socket with a fake client (CI-safe).

Exercises the Phase 2 verification: open/send/show/roster/archive via the daemon,
and error → exit-code projection — without needing a real app-server.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from outfitter.dispatch.contracts.registry import (
    CONTROL_EXEC_METHOD,
    op_schema_hash,
    registry_op_schema_hashes,
    registry_schema_hash,
)
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.daemon.control import ControlServer
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import AcceptedClient
from tests.fakes import make_ctx


async def _call(path: Path, method: str, params: dict[str, object]) -> dict[str, object]:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode())
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    parsed: dict[str, object] = json.loads(line)
    return parsed


@pytest_asyncio.fixture
async def socket_path(socket_dir: Path) -> AsyncIterator[Path]:
    store = await Registry.open()
    server = ControlServer(REGISTRY, make_ctx(store, AcceptedClient()))
    path = socket_dir / "dispatchd.sock"
    await server.serve(path)
    try:
        yield path
    finally:
        await server.close()
        await store.close()


async def test_open_send_show_roster_archive_via_daemon(socket_path: Path) -> None:
    opened = await _call(socket_path, "open", {"name": "alpha", "cwd": "/w"})
    assert _result(opened)["handle"] == "@alpha"

    sent = await _call(socket_path, "send", {"lane": "lane-1", "text": "hi"})
    assert _result(sent)["accepted"] is True

    shown = await _call(socket_path, "show", {"lane": "lane-1"})
    assert _result(shown)["handle"] == "@alpha"

    roster = await _call(socket_path, "roster", {})
    lanes = _result(roster)["lanes"]
    assert isinstance(lanes, list) and len(lanes) == 1

    archived = await _call(socket_path, "archive", {"target": "lane-1"})
    assert _result(archived)["status"] == "archived"


async def test_unknown_lane_projects_not_found(socket_path: Path) -> None:
    resp = await _call(socket_path, "show", {"lane": "ghost"})
    error = _error(resp)
    data = error["data"]
    assert isinstance(data, dict)
    assert data["exitCode"] == 4
    assert data["dispatchCode"] == "not_found"


async def test_keyed_delivery_receipt_round_trips_over_control_socket(socket_path: Path) -> None:
    await _call(socket_path, "open", {"name": "receipt", "cwd": "/w"})
    sent = await _call(
        socket_path, "send", {"lane": "lane-1", "text": "hi", "idempotency_key": "event-1"}
    )
    receipt = _result(sent)["delivery"]
    assert isinstance(receipt, dict)
    assert receipt["status"] == "accepted"
    assert isinstance(receipt["created_at"], str)
    assert isinstance(receipt["updated_at"], str)
    fetched = await _call(socket_path, "delivery-get", {"receipt_id": receipt["id"]})
    assert _result(fetched) == receipt


async def test_invalid_input_projects_validation_error(socket_path: Path) -> None:
    resp = await _call(socket_path, "open", {})  # missing required 'name'
    data = _error(resp)["data"]
    assert isinstance(data, dict)
    assert data["exitCode"] == 2


async def test_conflicting_new_permissions_project_validation_error(socket_path: Path) -> None:
    resp = await _call(
        socket_path,
        "new",
        {
            "name": "conflict",
            "permission_profile": ":workspace",
            "sandbox": "read-only",
        },
    )
    data = _error(resp)["data"]
    assert isinstance(data, dict)
    assert data["exitCode"] == 2
    assert data["dispatchCode"] == "validation"


async def test_unknown_op_is_method_not_found(socket_path: Path) -> None:
    resp = await _call(socket_path, "frobnicate", {})
    assert _error(resp)["code"] == -32601


async def test_checked_execution_validates_receiving_op_hash_before_handler(
    socket_path: Path,
) -> None:
    op = REGISTRY.get("open")
    opened = await _call(
        socket_path,
        CONTROL_EXEC_METHOD,
        {
            "op": "open",
            "params": {"name": "checked", "cwd": "/w"},
            "op_schema_hash": op_schema_hash(op),
        },
    )

    assert _result(opened)["handle"] == "@checked"


async def test_checked_execution_rejects_mismatched_hash_without_calling_handler(
    socket_path: Path,
) -> None:
    rejected = await _call(
        socket_path,
        CONTROL_EXEC_METHOD,
        {
            "op": "open",
            "params": {"name": "unchecked", "cwd": "/w"},
            "op_schema_hash": "stale",
        },
    )

    error = _error(rejected)
    data = error["data"]
    assert isinstance(data, dict)
    assert data["dispatchCode"] == "daemon_stale"
    assert data["exitCode"] == 8
    roster = await _call(socket_path, "roster", {})
    assert _result(roster)["lanes"] == []


async def test_checked_execution_rejects_malformed_envelope_without_calling_handler(
    socket_path: Path,
) -> None:
    rejected = await _call(
        socket_path,
        CONTROL_EXEC_METHOD,
        {"op": "open", "params": {"name": "unchecked", "cwd": "/w"}},
    )

    data = _error(rejected)["data"]
    assert isinstance(data, dict)
    assert data["dispatchCode"] == "validation"
    roster = await _call(socket_path, "roster", {})
    assert _result(roster)["lanes"] == []


async def test_checked_execution_rejects_unknown_op_without_calling_handler(
    socket_path: Path,
) -> None:
    rejected = await _call(
        socket_path,
        CONTROL_EXEC_METHOD,
        {"op": "frobnicate", "params": {}, "op_schema_hash": "hash"},
    )

    error = _error(rejected)
    data = error["data"]
    assert isinstance(data, dict)
    assert data["dispatchCode"] == "daemon_stale"
    assert data["exitCode"] == 8


async def test_control_metadata_reports_version_and_supported_ops(socket_path: Path) -> None:
    resp = await _call(socket_path, "__dispatch/metadata", {})
    result = _result(resp)
    assert result["protocol_version"] == 2
    assert isinstance(result["version"], str)
    supported_ops = result["supported_ops"]
    assert isinstance(supported_ops, list)
    assert "query" in supported_ops
    # The schema fingerprints clients compare against their own registry to
    # detect field-level drift a stale daemon would silently ignore: one per op
    # (the pre-flight gate) plus a whole-registry summary.
    assert result["registry_hash"] == registry_schema_hash(REGISTRY)
    assert result["op_schemas"] == registry_op_schema_hashes(REGISTRY)


def _result(message: dict[str, object]) -> dict[str, object]:
    result = message["result"]
    assert isinstance(result, dict)
    return result


def _error(message: dict[str, object]) -> dict[str, object]:
    error = message["error"]
    assert isinstance(error, dict)
    return error
