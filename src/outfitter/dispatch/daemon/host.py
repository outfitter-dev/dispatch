"""Daemon host wiring: own the app-server, host the core, serve the control
socket. Phase 5 adds supervision/restart and ``up``/``down``/``status``.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import structlog

from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.transport import StdioTransport
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.registry.store import Registry

from .control import ControlServer


async def run_daemon(socket_path: Path, db_path: Path) -> None:
    """Start the app-server client + registry, then serve the control socket
    until cancelled."""
    transport = StdioTransport()
    await transport.start()
    client = AppServerClient(transport)
    await client.start()
    await client.initialize()
    store = await Registry.open(db_path)
    # mypy verifies AppServerClient satisfies the LaneClient protocol here.
    ctx = Ctx(client=client, registry=store, log=structlog.get_logger(), abort=asyncio.Event())
    server = ControlServer(REGISTRY, ctx)

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    srv = await server.serve(socket_path)
    try:
        async with srv:
            await srv.serve_forever()
    finally:
        await server.close()
        await store.close()
        await client.close()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()
