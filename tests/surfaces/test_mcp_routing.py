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
    opened = await handle_tool_call(socket_path, "open", {"name": "alpha", "cwd": "/w"})
    assert not opened.isError
    assert opened.structuredContent is not None
    assert opened.structuredContent["handle"] == "@alpha"

    sent = await handle_tool_call(socket_path, "send", {"lane": "lane-1", "text": "hi"})
    assert sent.structuredContent is not None
    assert sent.structuredContent["accepted"] is True

    roster = await handle_tool_call(socket_path, "roster", {})
    assert roster.structuredContent is not None
    assert len(roster.structuredContent["lanes"]) == 1


async def test_tool_call_error_projects_full_taxonomy_into_meta(socket_path: Path) -> None:
    result = await handle_tool_call(socket_path, "show", {"lane": "ghost"})
    assert result.isError is True
    assert result.meta is not None
    # The DispatchError taxonomy (NotFoundError) projects end-to-end into _meta:
    assert result.meta["code"] == 1004  # rpc_code
    assert result.meta["dispatchCode"] == "not_found"
    assert result.meta["exitCode"] == 4  # same exit code the CLI would use
