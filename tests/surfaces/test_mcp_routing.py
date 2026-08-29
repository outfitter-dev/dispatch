"""MCP tool calls route to the daemon end-to-end (fake client over a real socket)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from mcp.types import TextContent

from outfitter.dispatch.contracts.derive_mcp import derive_mcp_projection
from outfitter.dispatch.contracts.registry import CONTROL_META_METHOD, OpRegistry
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.daemon.control import ControlServer
from outfitter.dispatch.registry.store import Registry
from outfitter.dispatch.surfaces import mcp
from outfitter.dispatch.surfaces.mcp import _route_tool_call, call_daemon, handle_tool_call
from tests.fakes import make_ctx

_IDENTITY_FIELDS = {"lane", "ref", "id", "title", "handle", "managed", "source", "status", "cwd"}


@pytest_asyncio.fixture
async def socket_path(socket_dir: Path) -> AsyncIterator[Path]:
    store = await Registry.open()
    server = ControlServer(REGISTRY, make_ctx(store))
    path = socket_dir / "dispatchd.sock"
    await server.serve(path)
    try:
        yield path
    finally:
        await server.close()
        await store.close()


async def test_tool_calls_open_send_roster(socket_path: Path) -> None:
    opened = await handle_tool_call(
        socket_path, "dispatch_thread_write", {"op": "open", "name": "alpha", "cwd": "/w"}
    )
    assert not opened.isError
    assert opened.structuredContent is not None
    assert opened.structuredContent["handle"] == "@alpha"

    sent = await handle_tool_call(
        socket_path, "dispatch_thread_write", {"op": "send", "lane": "lane-1", "text": "hi"}
    )
    assert sent.structuredContent is not None
    assert sent.structuredContent["accepted"] is True
    assert set(sent.structuredContent) >= _IDENTITY_FIELDS
    assert sent.structuredContent["ref"] == opened.structuredContent["ref"]

    roster = await handle_tool_call(socket_path, "dispatch_thread_read", {"op": "roster"})
    assert roster.structuredContent is not None
    assert len(roster.structuredContent["lanes"]) == 1


async def test_tool_calls_transcript_goal_and_compact(socket_path: Path) -> None:
    opened = await handle_tool_call(
        socket_path, "dispatch_thread_write", {"op": "open", "name": "alpha", "cwd": "/w"}
    )
    assert not opened.isError

    goal = await handle_tool_call(
        socket_path,
        "dispatch_thread_write",
        {"op": "goal_set", "lane": "lane-1", "objective": "ship"},
    )
    assert goal.structuredContent is not None
    assert goal.structuredContent["goal"]["objective"] == "ship"
    assert set(goal.structuredContent) >= _IDENTITY_FIELDS

    got = await handle_tool_call(
        socket_path,
        "dispatch_thread_read",
        {"op": "goal_get", "lane": "lane-1"},
    )
    assert got.structuredContent is not None
    assert got.structuredContent["goal"]["status"] == "active"
    assert set(got.structuredContent) >= _IDENTITY_FIELDS

    transcript = await handle_tool_call(
        socket_path,
        "dispatch_thread_read",
        {"op": "transcript", "lane": "lane-1", "limit": 5},
    )
    assert transcript.structuredContent is not None
    assert transcript.structuredContent["items"] == []
    assert set(transcript.structuredContent) >= _IDENTITY_FIELDS

    compact = await handle_tool_call(
        socket_path,
        "dispatch_thread_write",
        {"op": "compact", "lane": "lane-1"},
    )
    assert compact.structuredContent is not None
    assert compact.structuredContent["accepted"] is True
    assert set(compact.structuredContent) >= _IDENTITY_FIELDS

    synced = await handle_tool_call(
        socket_path,
        "dispatch_thread_write",
        {"op": "sync", "lane": "lane-1"},
    )
    assert synced.structuredContent is not None
    assert synced.structuredContent["sync"]["state"] == "metadata"
    assert set(synced.structuredContent) >= _IDENTITY_FIELDS


async def test_tool_call_error_projects_full_taxonomy_into_meta(socket_path: Path) -> None:
    result = await handle_tool_call(
        socket_path, "dispatch_thread_read", {"op": "show", "lane": "ghost"}
    )
    assert result.isError is True
    assert result.meta is not None
    # The DispatchError taxonomy (NotFoundError) projects end-to-end into _meta:
    assert result.meta["code"] == 1004  # rpc_code
    assert result.meta["dispatchCode"] == "not_found"
    assert result.meta["exitCode"] == 4  # same exit code the CLI would use


async def test_new_permission_conflict_projects_validation_taxonomy(
    socket_path: Path,
) -> None:
    result = await handle_tool_call(
        socket_path,
        "dispatch_thread_write",
        {
            "op": "new",
            "name": "conflict",
            "permission_profile": ":workspace",
            "sandbox": "read-only",
        },
    )

    assert result.isError is True
    assert result.meta is not None
    assert result.meta["dispatchCode"] == "validation"
    assert result.meta["exitCode"] == 2


async def test_daemon_read_usage_routes_to_same_authored_operation(socket_path: Path) -> None:
    result = await handle_tool_call(
        socket_path,
        "dispatch_daemon_read",
        {"op": "usage", "refresh": False},
    )

    assert result.isError is False
    assert result.structuredContent == {
        "refreshed_providers": [],
        "observations": [],
        "hint": "run dispatch usage without --no-refresh to refresh local providers",
    }


async def test_daemon_read_permissions_routes_to_same_authored_operation(
    socket_path: Path,
) -> None:
    result = await handle_tool_call(
        socket_path,
        "dispatch_daemon_read",
        {"op": "permissions", "cwd": "/work", "refresh": False},
    )

    assert result.isError is False
    assert result.structuredContent == {
        "cwd": "/work",
        "refreshed_at": None,
        "source": "registry",
        "catalog_state": "empty",
        "hint": "run dispatch permissions without --no-refresh to refresh the catalog",
        "profiles": [],
    }


async def test_tool_call_refuses_stale_daemon_only_for_skewed_op(socket_dir: Path) -> None:
    """A daemon whose schema for the invoked op differs (e.g. it predates
    ``provider`` on ``new``) must never receive that input — its Pydantic models
    would silently drop the fields it does not know. Ops whose schemas match
    (e.g. ``open``, ``roster``) stay usable against the same stale daemon."""
    stale_registry = OpRegistry()
    for op in REGISTRY:
        if op.id != "new":  # the stale daemon does not know this op's schema
            stale_registry.register(op)

    store = await Registry.open()
    server = ControlServer(stale_registry, make_ctx(store))
    path = socket_dir / "dispatchd.sock"
    await server.serve(path)
    try:
        result = await handle_tool_call(
            path,
            "dispatch_thread_write",
            {"op": "new", "name": "alpha", "cwd": "/w"},
        )

        assert result.isError is True
        assert result.meta is not None
        assert result.meta["dispatchCode"] == "daemon_stale"
        first = result.content[0]
        assert isinstance(first, TextContent)
        assert "dispatch down && dispatch up" in first.text
        # The stale daemon never executed the op.
        roster = await call_daemon(path, "roster", {})
        roster_result = roster.get("result")
        assert isinstance(roster_result, dict)
        assert roster_result["lanes"] == []

        # Hash-matching ops are still forwarded despite the drifted ``new``.
        opened = await handle_tool_call(
            path, "dispatch_thread_write", {"op": "open", "name": "alpha", "cwd": "/w"}
        )
        assert opened.isError is False
        listed = await handle_tool_call(path, "dispatch_thread_read", {"op": "roster"})
        assert listed.isError is False
        assert listed.structuredContent is not None
        assert len(listed.structuredContent["lanes"]) == 1
    finally:
        await server.close()
        await store.close()


async def test_tool_call_daemon_predating_handshake_blocks_all_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon without the metadata method (<= 0.8.1) is blocked for ALL ops
    — writes, baseline writes (``stop``), and reads (``roster``). Deliberate
    policy change: reads used to pass here, but sdist evidence shows 0.8.1's
    read inputs already drifted (``roster`` had no ``parent``), so a read
    would silently return the wrong lanes."""
    forwarded: list[str] = []

    async def fake_call_daemon(
        _socket_path: Path, method: str, _params: dict[str, object], _timeout: float = 30.0
    ) -> dict[str, object]:
        if method == CONTROL_META_METHOD:
            return {"id": 1, "error": {"code": -32601, "message": "unknown op"}}
        forwarded.append(method)
        return {"id": 1, "result": {"lanes": []}}

    monkeypatch.setattr(mcp, "call_daemon", fake_call_daemon)

    blocked = await handle_tool_call(
        Path("/nonexistent.sock"),
        "dispatch_thread_write",
        {"op": "new", "name": "alpha", "cwd": "/w", "provider": "claude"},
    )
    assert blocked.isError is True
    assert blocked.meta is not None
    assert blocked.meta["dispatchCode"] == "daemon_stale"

    # Reads too: the version floor cannot be checked without a version.
    listed = await handle_tool_call(
        Path("/nonexistent.sock"), "dispatch_thread_read", {"op": "roster"}
    )
    assert listed.isError is True
    assert listed.meta is not None
    assert listed.meta["dispatchCode"] == "daemon_stale"

    # ``stop`` matches the parent baseline, but this daemon reports no version
    # at all — the baseline proves nothing about it, so the write is blocked.
    stopped = await handle_tool_call(
        Path("/nonexistent.sock"), "dispatch_thread_write", {"op": "stop", "lane": "@a"}
    )
    assert stopped.isError is True
    assert stopped.meta is not None
    assert stopped.meta["dispatchCode"] == "daemon_stale"

    # ``new-plan`` is read-intent but shares ``NewInput`` with ``new`` — a
    # pre-handshake daemon would silently drop ``provider`` and preview the
    # wrong launch, so it is blocked like the write op.
    plan_blocked = await handle_tool_call(
        Path("/nonexistent.sock"),
        "dispatch_thread_read",
        {"op": "new_plan", "name": "alpha", "cwd": "/w", "provider": "claude"},
    )
    assert plan_blocked.isError is True
    assert plan_blocked.meta is not None
    assert plan_blocked.meta["dispatchCode"] == "daemon_stale"
    assert forwarded == []  # the legacy daemon never saw ANY op input


async def test_tool_call_prehandshake_baseline_ops_gated_by_reported_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-handshake daemon (metadata without ``op_schemas``) gets baseline
    write ops (``stop``) only when it self-reports exactly the parent version;
    a version at or above ``READ_BASELINE_FLOOR`` keeps reads flowing but
    blocks the write, and a version below the floor blocks reads too."""
    from outfitter.dispatch.contracts.legacy_baseline import PARENT_VERSION

    forwarded: list[str] = []
    reported_version = PARENT_VERSION

    async def fake_call_daemon(
        _socket_path: Path, method: str, _params: dict[str, object], _timeout: float = 30.0
    ) -> dict[str, object]:
        if method == CONTROL_META_METHOD:
            return {
                "id": 1,
                "result": {
                    "protocol_version": 1,
                    "version": reported_version,
                    "supported_ops": [],
                },
            }
        forwarded.append(method)
        return {"id": 1, "result": {"lanes": []}}

    monkeypatch.setattr(mcp, "call_daemon", fake_call_daemon)

    # Parent-version daemon: the baseline proves ``stop`` parses identically.
    stopped = await handle_tool_call(
        Path("/nonexistent.sock"), "dispatch_thread_write", {"op": "stop", "lane": "@a"}
    )
    assert stopped.isError is False
    assert forwarded == ["stop"]

    # Older pre-handshake daemon (e.g. v0.8.2's ``send`` had no ``content``):
    # baseline write blocked with the actionable restart hint, reads still pass.
    reported_version = "0.10.0"
    blocked = await handle_tool_call(
        Path("/nonexistent.sock"), "dispatch_thread_write", {"op": "stop", "lane": "@a"}
    )
    assert blocked.isError is True
    assert blocked.meta is not None
    assert blocked.meta["dispatchCode"] == "daemon_stale"
    assert blocked.meta["exitCode"] == 8
    first = blocked.content[0]
    assert isinstance(first, TextContent)
    assert "version 0.10.0" in first.text
    assert "dispatch down && dispatch up" in first.text
    assert forwarded == ["stop"]

    listed = await handle_tool_call(
        Path("/nonexistent.sock"), "dispatch_thread_read", {"op": "roster"}
    )
    assert listed.isError is False
    assert forwarded == ["stop", "roster"]

    # Below the read baseline floor (v0.9.0's ``usage`` output predates the
    # provider runtime summary): reads are blocked too.
    reported_version = "0.9.0"
    read_blocked = await handle_tool_call(
        Path("/nonexistent.sock"), "dispatch_thread_read", {"op": "roster"}
    )
    assert read_blocked.isError is True
    assert read_blocked.meta is not None
    assert read_blocked.meta["dispatchCode"] == "daemon_stale"
    first_read = read_blocked.content[0]
    assert isinstance(first_read, TextContent)
    assert "version 0.9.0" in first_read.text
    assert forwarded == ["stop", "roster"]


async def test_tool_call_rejects_unknown_grouped_action(socket_path: Path) -> None:
    result = await handle_tool_call(socket_path, "dispatch_thread_read", {"op": "send"})
    assert result.isError is True
    assert result.meta is not None
    assert result.meta["dispatchCode"] == "mcp_route_error"


def test_tool_call_routes_intro_with_caller_thread_id(monkeypatch: pytest.MonkeyPatch) -> None:
    projection = derive_mcp_projection(REGISTRY)
    monkeypatch.setenv("CODEX_THREAD_ID", "sender-thread")

    route = _route_tool_call(
        projection,
        "dispatch_thread_write",
        {"op": "send", "lane": "@docs", "text": "hi", "intro": True},
    )

    assert route == (
        "send",
        {
            "lane": "@docs",
            "text": "hi",
            "intro": True,
            "caller_thread_id": "sender-thread",
        },
    )
