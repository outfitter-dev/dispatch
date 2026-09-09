"""Acknowledged work remains accepted when reconnect misses its completion event."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.daemon.supervisor import Supervisor
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import HistoryClient
from tests.fakes import make_ctx


class RestoredClient(HistoryClient):
    def __init__(self) -> None:
        super().__init__()
        self.closed = asyncio.Event()
        self.lose_ack = False

    async def wait_closed(self) -> None:
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()


@pytest.mark.parametrize("history_visible", [True, False])
async def test_reconnect_recovers_accepted_work_without_resubmitting(
    tmp_path: Path,
    history_visible: bool,
) -> None:
    path = tmp_path / "registry.db"
    store = await Registry.open(path)
    client = RestoredClient()
    supervisor: Supervisor | None = None
    task: asyncio.Task[None] | None = None
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        ctx = make_ctx(store, client)
        first = await handlers.send_message(
            SendInput(lane="target", text="first", idempotency_key="one"), ctx
        )
        later = await handlers.send_message(
            SendInput(lane="target", text="later", mode="queue", idempotency_key="two"), ctx
        )
        assert first.delivery is not None and first.delivery.status == "accepted"
        assert later.delivery is not None and later.delivery.status == "queued"
        await store.close()
        store = await Registry.open(path)
        ctx = make_ctx(store, client)
        client.visible = history_visible
        client.read_result = {"thread": {"id": "target", "status": {"type": "idle"}}}

        async def make_client() -> RestoredClient:
            return client

        async def events() -> None:
            await asyncio.Event().wait()

        supervisor = Supervisor(ctx, make_client, events, backoff=0)
        task = asyncio.create_task(supervisor.supervise(client))
        async with asyncio.timeout(1):
            while (await store.get_delivery(later.delivery.id)).status != "accepted":
                await asyncio.sleep(0.01)
        receipt = await store.get_delivery(first.delivery.id)
        assert receipt.status == ("completed" if history_visible else "accepted")
        assert len([c for c in client.calls if c[0] == "turn_start"]) == 2
    finally:
        if supervisor is not None:
            await supervisor.stop()
        if task is not None:
            await task
        await store.close()
