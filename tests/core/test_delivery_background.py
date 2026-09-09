"""Delivery behavior through public handlers and provider evidence."""

from __future__ import annotations

import asyncio

import pytest

from outfitter.dispatch.client.models import (
    SortDirection,
    ThreadTurnsPage,
    TurnItemsView,
)
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import HistoryClient
from tests.fakes import make_ctx


@pytest.mark.asyncio
async def test_hold_blocks_later_queue_but_not_another_thread_then_releases() -> None:
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_receipt
    from outfitter.dispatch.core.queue import drain_next_queued_message

    store = await Registry.open()
    try:
        for name in ("target", "other"):
            await store.add_lane(id=name, handle=f"@{name}", source="own", status="idle")
        client = HistoryClient()
        ctx = make_ctx(store, client)
        ack = await handlers.send_message(
            SendInput(lane="target", text="first", idempotency_key="one"),
            ctx,
        )
        assert ack.delivery is not None
        await store.mark_lane_idle("target")  # Idle is not evidence of non-delivery.
        later = await handlers.send_message(
            SendInput(lane="target", text="later", mode="queue", idempotency_key="two"),
            ctx,
        )
        assert later.delivery is not None
        assert later.delivery.status == "queued"
        assert not await drain_next_queued_message(ctx, "target")
        client.lose_ack = False
        other = await handlers.send_message(
            SendInput(lane="other", text="unrelated", idempotency_key="three"),
            ctx,
        )
        assert other.delivery is not None and other.delivery.status == "accepted"
        assert await store.lane_delivery_held("target")
        await reconcile_receipt(ack.delivery.id, ctx)
        assert await drain_next_queued_message(ctx, "target")
        assert (await store.get_delivery(later.delivery.id)).status == "accepted"
        starts = [kw for name, kw in client.calls if name == "turn_start"]
        assert len(starts) == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_background_check_releases_and_drains_later_queue() -> None:
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_pending

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = HistoryClient()
        ctx = make_ctx(store, client)
        await handlers.send_message(
            SendInput(lane="target", text="first", idempotency_key="one"), ctx
        )
        later = await handlers.send_message(
            SendInput(lane="target", text="later", mode="queue", idempotency_key="two"), ctx
        )
        assert later.delivery is not None and later.delivery.status == "queued"
        client.read_result = {"thread": {"id": "target", "status": {"type": "idle"}}}
        client.lose_ack = False
        await reconcile_pending(ctx)
        assert (await store.get_delivery(later.delivery.id)).status == "accepted"
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slow_history_on_one_thread_does_not_block_another_queue() -> None:
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_pending

    release = asyncio.Event()

    class SlowHistoryClient(HistoryClient):
        async def thread_turns_list(
            self,
            thread_id: str,
            *,
            cursor: str | None = None,
            limit: int | None = None,
            sort_direction: SortDirection | None = None,
            items_view: TurnItemsView | None = None,
        ) -> ThreadTurnsPage:
            if thread_id == "target":
                await release.wait()
            return await super().thread_turns_list(
                thread_id,
                cursor=cursor,
                limit=limit,
                sort_direction=sort_direction,
                items_view=items_view,
            )

    store = await Registry.open()
    task: asyncio.Task[None] | None = None
    try:
        for name in ("target", "other"):
            await store.add_lane(id=name, handle=f"@{name}", source="own", status="idle")
        client = SlowHistoryClient()
        ctx = make_ctx(store, client)
        await handlers.send_message(
            SendInput(lane="target", text="first", idempotency_key="one"), ctx
        )
        await store.update_lane_status("other", "busy")
        queued = await handlers.send_message(
            SendInput(lane="other", text="later", mode="queue", idempotency_key="two"), ctx
        )
        assert queued.delivery is not None
        await store.mark_lane_idle("other")
        client.lose_ack = False
        task = asyncio.create_task(reconcile_pending(ctx))
        async with asyncio.timeout(0.5):
            while (await store.get_delivery(queued.delivery.id)).status != "accepted":
                await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
        if task is not None:
            await task
        await store.close()
