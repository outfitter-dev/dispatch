"""Daemon host wiring: own the app-server, host the core, serve the control
socket. Phase 5 adds supervision/restart and ``up``/``down``/``status``.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path

import structlog

from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.transport import StdioTransport
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.scheduler import Scheduler
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.store import Registry

from .control import ControlServer


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    runner = TriggerRunner(ctx, _utcnow)
    reactor = Reactor(ctx, runner)
    scheduler = Scheduler(ctx, runner, _utcnow)

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    srv = await server.serve(socket_path)
    reactor_task = asyncio.create_task(reactor.run())
    scheduler_task = asyncio.create_task(scheduler.run())
    try:
        async with srv:
            await srv.serve_forever()
    finally:
        reactor_task.cancel()
        scheduler_task.cancel()
        for task in (reactor_task, scheduler_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await server.close()
        await store.close()
        await client.close()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()
