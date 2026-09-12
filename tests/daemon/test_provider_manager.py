"""Independent provider worker lifecycle and failure boundaries."""

from __future__ import annotations

import asyncio

import pytest
import structlog

from outfitter.dispatch.contracts.errors import CapabilityUnavailableError
from outfitter.dispatch.core.providers import (
    CodexLaneAdapter,
    ProviderAction,
    ProviderAvailability,
    ProviderBindingFacts,
    ProviderDurability,
    ProviderRouter,
)
from outfitter.dispatch.daemon.provider_manager import (
    ProviderManager,
    ProviderWorker,
    SharedCoreFailure,
)
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID
from tests.fakes import FakeLaneClient


class _SyntheticAdapter(CodexLaneAdapter):
    def __init__(
        self,
        client: FakeLaneClient,
        *,
        provider: str,
        binding_id: str,
        generation: str,
    ) -> None:
        super().__init__(client, binding_id=binding_id)
        self.facts = ProviderBindingFacts(
            provider=provider,
            binding_id=binding_id,
            supported_actions=frozenset({ProviderAction.READ}),
            availability=ProviderAvailability(ready=True, generation=generation),
            durability=ProviderDurability(),
        )


@pytest.mark.parametrize(
    ("failed_provider", "failed_binding", "ready_provider", "ready_binding"),
    [
        ("hermes", "local", "codex", DEFAULT_CODEX_BINDING_ID),
        ("codex", DEFAULT_CODEX_BINDING_ID, "hermes", "local"),
    ],
)
async def test_failed_provider_worker_does_not_cancel_ready_sibling(
    failed_provider: str,
    failed_binding: str,
    ready_provider: str,
    ready_binding: str,
) -> None:
    router = ProviderRouter(())
    manager = ProviderManager(router, structlog.get_logger())
    ready_client = FakeLaneClient()
    ready_closed = asyncio.Event()

    async def run_ready() -> None:
        manager.mark_ready(
            ready_provider,
            ready_binding,
            _SyntheticAdapter(
                ready_client,
                provider=ready_provider,
                binding_id=ready_binding,
                generation=f"{ready_provider}-1",
            ),
        )
        await ready_closed.wait()

    async def crash_provider() -> None:
        raise RuntimeError(f"{failed_provider} gateway unavailable")

    async def close_ready(_generation: str | None) -> None:
        ready_closed.set()

    async def close_failed(_generation: str | None) -> None:
        return None

    manager.add(
        ProviderWorker(
            provider=ready_provider,
            binding_id=ready_binding,
            supported_actions=frozenset({ProviderAction.READ}),
            durability=ProviderDurability(),
            owns_process=True,
            run=run_ready,
            close=close_ready,
        )
    )
    manager.add(
        ProviderWorker(
            provider=failed_provider,
            binding_id=failed_binding,
            supported_actions=frozenset({ProviderAction.READ}),
            durability=ProviderDurability(),
            owns_process=False,
            run=crash_provider,
            close=close_failed,
        )
    )

    manager.start()
    async with asyncio.timeout(1):
        while manager.snapshot(failed_provider, failed_binding).state != "unavailable":
            await asyncio.sleep(0)

    assert manager.snapshot(ready_provider, ready_binding).state == "ready"
    assert (
        router.route_binding(ready_provider, ready_binding, ProviderAction.READ).adapter.client
        is ready_client
    )
    with pytest.raises(CapabilityUnavailableError, match="gateway unavailable"):
        router.route_binding(failed_provider, failed_binding, ProviderAction.READ)

    await manager.stop()


async def test_shutdown_closes_external_client_without_stopping_runtime() -> None:
    router = ProviderRouter(())
    manager = ProviderManager(router, structlog.get_logger())
    connection_closed = False
    closed_generation: str | None = None
    runtime_alive = True

    async def run_external() -> None:
        manager.mark_ready(
            "hermes",
            "external",
            _SyntheticAdapter(
                FakeLaneClient(),
                provider="hermes",
                binding_id="external",
                generation="external-7",
            ),
        )
        await asyncio.Event().wait()

    async def close_connection(generation: str | None) -> None:
        nonlocal closed_generation, connection_closed
        connection_closed = True
        closed_generation = generation

    manager.add(
        ProviderWorker(
            provider="hermes",
            binding_id="external",
            supported_actions=frozenset({ProviderAction.READ}),
            durability=ProviderDurability(),
            owns_process=False,
            run=run_external,
            close=close_connection,
        )
    )
    manager.start()
    async with asyncio.timeout(1):
        while manager.snapshot("hermes", "external").state != "ready":
            await asyncio.sleep(0)

    await manager.stop()

    snapshot = manager.snapshot("hermes", "external")
    assert connection_closed is True
    assert closed_generation == "external-7"
    assert runtime_alive is True
    assert snapshot.state == "stopped"
    assert snapshot.owns_process is False
    assert snapshot.connection_generation == "external-7"
    assert snapshot.supported_actions == ("read",)
    assert snapshot.durability == ProviderDurability()


async def test_shutdown_closes_generation_that_becomes_ready_during_stop() -> None:
    router = ProviderRouter(())
    manager = ProviderManager(router, structlog.get_logger())
    release_connection = asyncio.Event()
    close_started = asyncio.Event()
    ready_attempted = asyncio.Event()
    late_generation_closed = asyncio.Event()

    async def connect_late() -> None:
        await release_connection.wait()
        manager.mark_ready(
            "hermes",
            "external",
            _SyntheticAdapter(
                FakeLaneClient(),
                provider="hermes",
                binding_id="external",
                generation="external-late",
            ),
        )
        ready_attempted.set()
        await asyncio.Event().wait()

    async def close(generation: str | None) -> None:
        if generation is None:
            close_started.set()
            await ready_attempted.wait()
        elif generation == "external-late":
            late_generation_closed.set()

    manager.add(
        ProviderWorker(
            provider="hermes",
            binding_id="external",
            supported_actions=frozenset({ProviderAction.READ}),
            durability=ProviderDurability(),
            owns_process=False,
            run=connect_late,
            close=close,
        )
    )
    manager.start()
    stop_task = asyncio.create_task(manager.stop())
    await asyncio.wait_for(close_started.wait(), timeout=1)
    release_connection.set()
    await asyncio.wait_for(stop_task, timeout=1)

    assert late_generation_closed.is_set()
    assert manager.snapshot("hermes", "external").state == "stopped"
    with pytest.raises(CapabilityUnavailableError):
        router.route_binding("hermes", "external", ProviderAction.READ)


async def test_worker_crash_preserves_generation_for_exact_shutdown() -> None:
    router = ProviderRouter(())
    manager = ProviderManager(router, structlog.get_logger())
    closed_generation: str | None = None

    async def run_then_crash() -> None:
        manager.mark_ready(
            "synthetic",
            "owned",
            _SyntheticAdapter(
                FakeLaneClient(),
                provider="synthetic",
                binding_id="owned",
                generation="owned-3",
            ),
        )
        raise RuntimeError("worker crashed")

    async def close(generation: str | None) -> None:
        nonlocal closed_generation
        closed_generation = generation

    manager.add(
        ProviderWorker(
            provider="synthetic",
            binding_id="owned",
            supported_actions=frozenset({ProviderAction.READ}),
            durability=ProviderDurability(),
            owns_process=True,
            run=run_then_crash,
            close=close,
        )
    )
    manager.start()
    async with asyncio.timeout(1):
        while manager.snapshot("synthetic", "owned").state != "unavailable":
            await asyncio.sleep(0)

    assert manager.snapshot("synthetic", "owned").connection_generation == "owned-3"
    await manager.stop()
    assert closed_generation == "owned-3"


async def test_shared_core_failure_is_fatal_instead_of_provider_local() -> None:
    router = ProviderRouter(())
    manager = ProviderManager(router, structlog.get_logger())

    async def fail_shared_core() -> None:
        raise SharedCoreFailure("registry unavailable")

    async def close(_generation: str | None) -> None:
        return None

    manager.add(
        ProviderWorker(
            provider="codex",
            binding_id=DEFAULT_CODEX_BINDING_ID,
            supported_actions=frozenset({ProviderAction.READ}),
            durability=ProviderDurability(),
            owns_process=True,
            run=fail_shared_core,
            close=close,
        )
    )
    manager.start()

    with pytest.raises(SharedCoreFailure, match="registry unavailable"):
        await asyncio.wait_for(manager.wait_fatal(), timeout=1)

    assert manager.snapshot("codex", DEFAULT_CODEX_BINDING_ID).state == "unavailable"
    await manager.stop()


def test_fenced_provider_generation_change_publishes_replacement_binding() -> None:
    router = ProviderRouter(())
    manager = ProviderManager(router, structlog.get_logger())

    async def idle() -> None:
        await asyncio.Event().wait()

    async def close(_generation: str | None) -> None:
        return None

    manager.add(
        ProviderWorker(
            provider="hermes",
            binding_id="owned",
            supported_actions=frozenset({ProviderAction.READ}),
            durability=ProviderDurability(),
            owns_process=True,
            run=idle,
            close=close,
        )
    )
    manager.mark_ready(
        "hermes",
        "owned",
        _SyntheticAdapter(
            FakeLaneClient(),
            provider="hermes",
            binding_id="owned",
            generation="hermes-1",
        ),
    )
    manager.mark_unavailable("hermes", "owned", "gateway exited", generation="hermes-1")

    manager.mark_ready(
        "hermes",
        "owned",
        _SyntheticAdapter(
            FakeLaneClient(),
            provider="hermes",
            binding_id="owned",
            generation="hermes-2",
        ),
    )

    assert manager.snapshot("hermes", "owned").state == "ready"
    route = router.route_binding("hermes", "owned", ProviderAction.READ)
    assert route.availability.generation == "hermes-2"
