"""Completed delivery evidence must not make a failed readiness read permanent."""

from __future__ import annotations

from typing import Literal

import pytest

from outfitter.dispatch.client.models import ThreadTurn
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.core.delivery import reconcile_receipt_request
from outfitter.dispatch.core.delivery_reconciliation import (
    MAX_READINESS_CHECKS,
    reconcile_pending,
    reconcile_receipt,
)
from outfitter.dispatch.core.handlers import send_message
from outfitter.dispatch.core.models import DeliveryLookupInput, SendInput
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import HistoryClient
from tests.fakes import make_ctx


@pytest.mark.parametrize("fault", ["timeout", "wrong-thread", "malformed-status", "race"])
@pytest.mark.parametrize("keyed", [False, True])
async def test_manual_completed_reconcile_retries_readiness_and_drains(
    fault: str, keyed: bool
) -> None:
    store = await Registry.open()

    class ReadinessClient(HistoryClient):
        failures_remaining = MAX_READINESS_CHECKS

        async def thread_read(
            self, thread_id: str, include_turns: bool = False
        ) -> dict[str, object]:
            self._record("thread_read", thread_id=thread_id, include_turns=include_turns)
            if self.failures_remaining:
                self.failures_remaining -= 1
                if fault == "timeout":
                    raise TimeoutError("synthetic metadata timeout")
                if fault == "wrong-thread":
                    return {"thread": {"id": "other", "status": {"type": "idle"}}}
                if fault == "malformed-status":
                    return {"thread": {"id": thread_id, "status": "idle"}}
                await store.touch_lane_event(thread_id)
            return {"thread": {"id": thread_id, "status": {"type": "idle"}}}

    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = ReadinessClient()
        ctx = make_ctx(store, client)
        first = await send_message(
            SendInput(lane="target", text="first", idempotency_key="first"), ctx
        )
        assert first.delivery is not None
        await send_message(
            SendInput(
                lane="target",
                text="later",
                mode="queue",
                idempotency_key="later" if keyed else None,
            ),
            ctx,
        )
        queued = await store.next_pending_message("target")
        assert queued is not None
        client.lose_ack = False

        await reconcile_receipt(first.delivery.id, ctx)
        completed = await store.get_delivery(first.delivery.id)
        assert completed.status == "completed"
        assert (await store.get_lane("target")).status == "busy"
        assert (await store.get_queued_message(queued.id)).status == "pending"

        client.failures_remaining = 0
        result = await reconcile_receipt_request(
            DeliveryLookupInput(receipt_id=first.delivery.id), ctx
        )

        assert result.status == "completed"
        assert result.reconciliation_attempts == completed.reconciliation_attempts
        assert len([call for call in client.calls if call[0] == "thread_turns_list"]) == 1
        starts = [call for call in client.calls if call[0] == "turn_start"]
        assert len(starts) == 2
        assert (await store.get_queued_message(queued.id)).status == "sent"
    finally:
        await store.close()


async def test_one_transient_readiness_fault_recovers_during_background_check() -> None:
    class ReadinessClient(HistoryClient):
        reads = 0

        async def thread_read(
            self, thread_id: str, include_turns: bool = False
        ) -> dict[str, object]:
            self.reads += 1
            if self.reads == 1:
                raise TimeoutError("synthetic transient timeout")
            return {"thread": {"id": thread_id, "status": {"type": "idle"}}}

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = ReadinessClient()
        ctx = make_ctx(store, client)
        await send_message(SendInput(lane="target", text="first", idempotency_key="first"), ctx)
        later = await send_message(
            SendInput(lane="target", text="later", mode="queue", idempotency_key="later"), ctx
        )
        assert later.delivery is not None
        client.lose_ack = False

        await reconcile_pending(ctx)

        assert client.reads == 2
        assert (await store.get_delivery(later.delivery.id)).status == "accepted"
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 2
    finally:
        await store.close()


@pytest.mark.parametrize("status", ["failed", "interrupted"])
async def test_unsuccessful_history_never_refreshes_readiness_or_drains(
    status: Literal["failed", "interrupted"], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def found_unsuccessful(
        ctx: Ctx, lane: str, delivery_id: str, expected: str
    ) -> tuple[ThreadTurn, str]:
        return ThreadTurn(id="turn-1", status=status), "exact provider user message"

    monkeypatch.setattr(
        "outfitter.dispatch.core.delivery_reconciliation._find_arrival", found_unsuccessful
    )
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = HistoryClient()
        client.read_result = {"thread": {"id": "target", "status": {"type": "idle"}}}
        ctx = make_ctx(store, client)
        first = await send_message(
            SendInput(lane="target", text="first", idempotency_key="first"), ctx
        )
        assert first.delivery is not None
        await send_message(SendInput(lane="target", text="later", mode="queue"), ctx)

        receipt = await reconcile_receipt_request(
            DeliveryLookupInput(receipt_id=first.delivery.id), ctx
        )

        assert receipt.status == "accepted" and receipt.execution_status == status
        assert (await store.get_lane("target")).status == "busy"
        assert await store.next_pending_message("target") is not None
        assert not any(call[0] == "thread_read" for call in client.calls)
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
    finally:
        await store.close()


@pytest.mark.parametrize("race", [False, True])
async def test_completed_readiness_does_not_override_current_busy_or_newer_event(
    race: bool,
) -> None:
    store = await Registry.open()

    class BusyClient(HistoryClient):
        async def thread_read(
            self, thread_id: str, include_turns: bool = False
        ) -> dict[str, object]:
            self._record("thread_read", thread_id=thread_id, include_turns=include_turns)
            if race:
                await store.record_turn_started(thread_id, "newer-turn")
            return {"thread": {"id": thread_id, "status": {"type": "idle" if race else "active"}}}

    try:
        await store.add_lane(id="target", handle="@target", source="own", status="busy")
        receipt, _ = await store.reserve_delivery(
            key="finished", lane="target", mode="send", text="finished", payload="{}"
        )
        await store.update_delivery(
            receipt.id, status="completed", execution_status="completed", turn_id="old-turn"
        )
        client = BusyClient()
        ctx = make_ctx(store, client)
        queued = await store.enqueue_message(lane="target", text="later")

        await reconcile_receipt_request(DeliveryLookupInput(receipt_id=receipt.id), ctx)

        assert (await store.get_lane("target")).status == "busy"
        assert (await store.get_queued_message(queued.id)).status == "pending"
        assert len([call for call in client.calls if call[0] == "thread_read"]) == 1
        assert not any(call[0] in {"turn_start", "thread_turns_list"} for call in client.calls)
    finally:
        await store.close()
