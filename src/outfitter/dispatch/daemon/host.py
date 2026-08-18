"""Daemon host wiring: own the app-server (with supervision/restart), host the
core, serve the control socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import structlog

from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.transport import StdioTransport
from outfitter.dispatch.codex_compat import inspect_codex_binary
from outfitter.dispatch.config import capture_policy, runtime_policy
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.scheduler import Scheduler
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.store import Registry

from .control import ControlServer
from .supervisor import Supervisor


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _spawn_client() -> AppServerClient:
    transport = StdioTransport()
    await transport.start()
    client = AppServerClient(transport)
    await client.start()
    await client.initialize()
    return client


async def _warn_if_codex_below_floor(log: structlog.stdlib.BoundLogger) -> None:
    """Warn without blocking startup; the app-server remains forward-compatible by policy."""
    try:
        compatibility = await asyncio.to_thread(inspect_codex_binary)
    except (OSError, subprocess.SubprocessError, ValueError):
        return
    if not compatibility.supported:
        log.warning(
            "dispatchd.codex_version_below_floor",
            path=compatibility.path,
            version=compatibility.version,
            minimum_version=compatibility.minimum_version,
        )


async def run_daemon(socket_path: Path, db_path: Path) -> None:
    """Start the supervised app-server client + registry, then serve the control
    socket until cancelled. The supervisor restarts the app-server on crash."""
    store = await Registry.open(db_path)
    log = structlog.get_logger()
    await _warn_if_codex_below_floor(log)
    first_client = await _spawn_client()
    # mypy verifies AppServerClient satisfies the LaneClient protocol here.
    ctx = Ctx(
        client=first_client,
        registry=store,
        log=log,
        abort=asyncio.Event(),
        policy=runtime_policy(),
        capture=capture_policy(),
    )
    runner = TriggerRunner(ctx, _utcnow)
    scheduler = Scheduler(ctx, runner, _utcnow)
    server = ControlServer(REGISTRY, ctx)
    supervisor = Supervisor(ctx, _spawn_client, lambda: Reactor(ctx, runner).run())

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    await server.serve(socket_path)  # already accepting once started
    supervisor_task = asyncio.create_task(supervisor.supervise(first_client))
    scheduler_task = asyncio.create_task(scheduler.run())
    try:
        await stop.wait()  # serve until SIGTERM/SIGINT
    finally:
        log.info("dispatchd.shutting_down")
        await supervisor.stop()  # closes the client → terminates the app-server
        scheduler_task.cancel()
        for task in (supervisor_task, scheduler_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await server.close()
        await store.close()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()
