"""Supervisor: restart the app-server on crash and re-resume lanes (recoverable
daemon — the v0 DoD)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio

from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.daemon.supervisor import Supervisor
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeSupervisedClient, make_ctx

_T0 = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open()
    try:
        yield s
    finally:
        await s.close()


async def test_supervisor_restarts_and_reresumes_lanes_on_crash(store: Registry) -> None:
    await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")
    await store.add_lane(id="O1", handle="@own", source="own", status="idle")
    ctx = make_ctx(store)
    clients: list[FakeSupervisedClient] = []

    async def make_client() -> FakeSupervisedClient:
        client = FakeSupervisedClient()
        clients.append(client)
        return client

    runner = TriggerRunner(ctx, lambda: _T0)
    supervisor = Supervisor(ctx, make_client, lambda: Reactor(ctx, runner).run(), backoff=0)

    first = await make_client()
    task = asyncio.create_task(supervisor.supervise(first))
    await asyncio.sleep(0.05)

    # On first start, both persisted lanes are re-resumed and ctx.client is set.
    assert sorted(clients[0].resumed) == ["D1", "O1"]
    assert ctx.client is clients[0]

    # Simulate app-server crash (stdout EOF → wait_closed returns).
    clients[0].closed.set()
    await asyncio.sleep(0.05)

    # Supervisor started a fresh client and re-resumed the lanes on it.
    assert len(clients) == 2
    assert sorted(clients[1].resumed) == ["D1", "O1"]
    assert ctx.client is clients[1]

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_recovers_and_drains_idle_queue_on_start(store: Registry) -> None:
    await store.add_lane(id="O1", handle="@own", source="own", status="idle")
    queued = await store.enqueue_message(lane="O1", text="resume queued")
    assert await store.claim_queued_message(queued.id)
    ctx = make_ctx(store)
    clients: list[FakeSupervisedClient] = []

    async def make_client() -> FakeSupervisedClient:
        client = FakeSupervisedClient()
        clients.append(client)
        return client

    runner = TriggerRunner(ctx, lambda: _T0)
    supervisor = Supervisor(ctx, make_client, lambda: Reactor(ctx, runner).run(), backoff=0)

    first = await make_client()
    task = asyncio.create_task(supervisor.supervise(first))
    await asyncio.sleep(0.05)

    assert (await store.get_queued_message(queued.id)).status == "sent"
    assert any(
        name == "turn_start" and kw["text"] == "resume queued" for name, kw in clients[0].calls
    )

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)
