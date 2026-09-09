"""A failed reserved queue row cannot stop independent idle-thread recovery."""

from __future__ import annotations

import asyncio
import json

import pytest

from outfitter.dispatch.core.queue import drain_idle_queues, drain_next_queued_message
from outfitter.dispatch.registry.delivery import (
    DeliveryExecutionStatus,
    DeliveryReceipt,
    DeliveryStatus,
)
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import AcceptedClient
from tests.fakes import make_ctx


@pytest.mark.parametrize("fault", ["payload", "bookkeeping"])
async def test_reserved_failure_does_not_stop_other_idle_queue(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    store = await Registry.open()
    try:
        for lane in ("bad", "good"):
            await store.add_lane(id=lane, handle=f"@{lane}", source="own", status="idle")
        failed, _ = await store.reserve_delivery(
            key="bad",
            lane="bad",
            mode="queue",
            text="bad",
            payload=(
                "invalid json"
                if fault == "payload"
                else json.dumps({"text": "bad", "cwd": "/tmp", "settings": {}})
            ),
        )
        good = await store.enqueue_message(lane="good", text="good")
        update = store.update_delivery

        async def fail_ack_once(
            delivery_id: str,
            *,
            status: DeliveryStatus,
            turn_id: str | None = None,
            error: str | None = None,
            execution_status: DeliveryExecutionStatus | None = None,
        ) -> DeliveryReceipt:
            if delivery_id == failed.id and status == "accepted":
                raise RuntimeError("synthetic ACK bookkeeping failure")
            return await update(
                delivery_id,
                status=status,
                turn_id=turn_id,
                error=error,
                execution_status=execution_status,
            )

        if fault == "bookkeeping":
            monkeypatch.setattr(store, "update_delivery", fail_ack_once)
        client = AcceptedClient()
        ctx = make_ctx(store, client)

        assert await drain_idle_queues(ctx) == 1
        receipt = await store.get_delivery(failed.id)
        assert receipt.status == ("queued" if fault == "payload" else "ambiguous")
        assert (await store.get_queued_message(good.id)).status == "sent"
        assert failed.queue_id is not None
        assert (await store.get_queued_message(failed.queue_id)).status == (
            "pending" if fault == "payload" else "sending"
        )
        if fault == "bookkeeping":
            assert "synthetic ACK bookkeeping failure" in (receipt.error or "")
            assert await store.lane_delivery_held("bad")
        starts = [params for name, params in client.calls if name == "turn_start"]
        assert len(starts) == (1 if fault == "payload" else 2)
        assert (await store.get_lane("good")).status == "busy"
    finally:
        await store.close()


async def test_reserved_queue_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    from outfitter.dispatch.core import delivery

    async def cancelled(*args: object, **kwargs: object) -> bool:
        raise asyncio.CancelledError

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        await store.reserve_delivery(
            key=None, lane="target", mode="queue", text="hello", payload="{}"
        )
        monkeypatch.setattr(delivery, "submit_reserved", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await drain_next_queued_message(make_ctx(store), "target")
    finally:
        await store.close()
