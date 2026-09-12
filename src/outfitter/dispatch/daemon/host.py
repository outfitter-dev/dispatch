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
from outfitter.dispatch.client.transport import StdioTransport, UnixSocketTransport
from outfitter.dispatch.codex_compat import inspect_codex_binary
from outfitter.dispatch.config import (
    DEFAULT_HERMES_BINDING_ID,
    app_server_socket_path,
    capture_policy,
    hermes_binding_config,
    runtime_policy,
)
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.core.hermes import (
    HERMES_ACTIONS,
    HermesLaneAdapter,
    apply_hermes_activity_observation,
    apply_hermes_attention_observation,
    apply_hermes_delivery_observation,
    apply_hermes_transcript_observation,
    quarantine_stale_hermes_lanes,
)
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.core.providers import (
    ALL_CODEX_ACTIONS,
    ProviderBindingAdapter,
    ProviderDurability,
    ProviderRouter,
)
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.scheduler import Scheduler
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID, Registry

from .control import ControlServer
from .hermes_worker import HermesWorkerSupervisor
from .provider_manager import ProviderManager, ProviderWorker
from .supervisor import Supervisor


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _spawn_client() -> AppServerClient:
    transport = _configured_transport()
    client: AppServerClient | None = None
    try:
        await transport.start()
        client = AppServerClient(transport)
        await client.start()
        await client.initialize()
        return client
    except BaseException:
        with contextlib.suppress(Exception):
            if client is not None:
                await client.close()
            else:
                await transport.close()
        raise


def _configured_transport() -> StdioTransport | UnixSocketTransport:
    socket = app_server_socket_path()
    return UnixSocketTransport(socket) if socket is not None else StdioTransport()


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
    """Serve registry/control first, then supervise provider connections."""
    store = await Registry.open(db_path)
    log = structlog.get_logger()
    provider_router = ProviderRouter.unavailable_codex("Codex App Server is starting")
    ctx = Ctx(
        client=None,
        registry=store,
        log=log,
        abort=asyncio.Event(),
        policy=runtime_policy(),
        capture=capture_policy(),
        providers=provider_router,
    )
    runner = TriggerRunner(ctx, _utcnow)
    scheduler = Scheduler(ctx, runner, _utcnow)
    server = ControlServer(REGISTRY, ctx)
    provider_manager = ProviderManager(provider_router, log)
    supervisor = Supervisor(
        ctx,
        _spawn_client,
        lambda: Reactor(ctx, runner).run(),
        manager=provider_manager,
    )
    provider_manager.add(
        ProviderWorker(
            provider="codex",
            binding_id=DEFAULT_CODEX_BINDING_ID,
            supported_actions=ALL_CODEX_ACTIONS,
            durability=ProviderDurability(),
            owns_process=app_server_socket_path() is None,
            run=supervisor.supervise,
            close=supervisor.stop,
        )
    )
    try:
        hermes_binding = hermes_binding_config()
    except (OSError, ValueError) as exc:
        provider_router.register_unavailable(
            provider="hermes",
            binding_id=DEFAULT_HERMES_BINDING_ID,
            supported_actions=HERMES_ACTIONS,
            reason=f"invalid Hermes binding configuration: {exc}",
            durability=ProviderDurability(local_reservation=True, native_evidence=True),
        )
    else:
        if hermes_binding is not None:

            async def mark_hermes_ready(adapter: ProviderBindingAdapter) -> None:
                generation = adapter.facts.availability.generation
                if generation is None:
                    raise ValueError("ready Hermes adapter omitted its connection generation")
                await quarantine_stale_hermes_lanes(
                    store,
                    binding_id=hermes_binding.binding_id,
                    current_generation=generation,
                )
                provider_manager.mark_ready("hermes", DEFAULT_HERMES_BINDING_ID, adapter)

            hermes_supervisor = HermesWorkerSupervisor(
                hermes_binding,
                adapter_factory=lambda client, generation, _capabilities: HermesLaneAdapter(
                    client,
                    generation=generation,
                    observe=lambda observation: apply_hermes_delivery_observation(
                        store, provider_router, observation
                    ),
                    observe_attention=lambda observation: apply_hermes_attention_observation(
                        store, provider_router, observation
                    ),
                    observe_transcript=lambda observation: apply_hermes_transcript_observation(
                        store, provider_router, observation
                    ),
                    observe_activity=lambda observation: apply_hermes_activity_observation(
                        store, provider_router, observation
                    ),
                ),
                mark_ready=mark_hermes_ready,
            )
            provider_manager.add(
                hermes_supervisor.provider_worker(
                    supported_actions=HERMES_ACTIONS,
                    durability=ProviderDurability(
                        local_reservation=True,
                        native_evidence=True,
                    ),
                )
            )
    ctx.provider_manager = provider_manager

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    await server.serve(socket_path)  # already accepting once started
    provider_manager.start()
    scheduler_task = asyncio.create_task(scheduler.run())
    stop_task = asyncio.create_task(stop.wait())
    fatal_task = asyncio.create_task(provider_manager.wait_fatal())
    try:
        await _warn_if_codex_below_floor(log)
        done, _ = await asyncio.wait((stop_task, fatal_task), return_when=asyncio.FIRST_COMPLETED)
        if fatal_task in done:
            await fatal_task
    finally:
        log.info("dispatchd.shutting_down")
        await provider_manager.stop()
        scheduler_task.cancel()
        stop_task.cancel()
        fatal_task.cancel()
        await asyncio.gather(scheduler_task, stop_task, fatal_task, return_exceptions=True)
        await server.close()
        await store.close()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()
