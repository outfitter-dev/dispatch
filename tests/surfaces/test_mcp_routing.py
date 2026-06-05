"""MCP tool calls route to the daemon end-to-end (fake client over a real socket)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.daemon.control import ControlServer
from outfitter.dispatch.registry.store import Registry
from outfitter.dispatch.surfaces.mcp import handle_tool_call
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


async def test_tool_call_rejects_unknown_grouped_action(socket_path: Path) -> None:
    result = await handle_tool_call(socket_path, "dispatch_thread_read", {"op": "send"})
    assert result.isError is True
    assert result.meta is not None
    assert result.meta["dispatchCode"] == "mcp_route_error"
