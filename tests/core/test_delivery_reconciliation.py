"""Delivery behavior through public handlers and provider evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import HistoryClient
from tests.fakes import make_ctx


@pytest.mark.asyncio
async def test_exact_provider_history_resolves_lost_ack() -> None:
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_receipt

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = HistoryClient()
        ctx = make_ctx(store, client)
        ack = await handlers.send_message(
            SendInput(lane="target", text="hello", idempotency_key="event-1"),
            ctx,
        )
        assert ack.delivery is not None
        await reconcile_receipt(ack.delivery.id, ctx)
        receipt = await store.get_delivery(ack.delivery.id)
        assert receipt.status == "completed"
        assert receipt.turn_id == "turn-1"
        assert receipt.evidence_source == "history"
        assert receipt.evidence_received_at is not None
        assert receipt.evidence_partial is False
        assert not await store.lane_delivery_held("target")
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    ["missing", "truncated", "summary", "wrong-text", "assistant", "duplicate", "timeout"],
)
async def test_inconclusive_history_stops_checks_without_resend(variant: str) -> None:
    from outfitter.dispatch.core.delivery_reconciliation import MAX_CHECKS, reconcile_receipt

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = HistoryClient()
        client.variant = variant
        client.visible = variant != "missing"
        ctx = make_ctx(store, client)
        ack = await handlers.send_message(
            SendInput(lane="target", text="hello", idempotency_key="one"), ctx
        )
        assert ack.delivery is not None
        for _ in range(MAX_CHECKS + 2):
            await reconcile_receipt(ack.delivery.id, ctx)
        receipt = await store.get_delivery(ack.delivery.id)
        assert receipt.status == "ambiguous"
        assert receipt.evidence_source == "history"
        assert receipt.evidence_partial is True
        assert receipt.reconciliation_attempts == MAX_CHECKS
        assert "held" in (receipt.error or "")
        assert await store.lane_delivery_held("target")
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_late_visibility_resolves_after_an_inconclusive_check() -> None:
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_receipt

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = HistoryClient()
        client.visible = False
        ctx = make_ctx(store, client)
        ack = await handlers.send_message(
            SendInput(lane="target", text="hello", idempotency_key="one"), ctx
        )
        assert ack.delivery is not None
        await reconcile_receipt(ack.delivery.id, ctx)
        assert (await store.get_delivery(ack.delivery.id)).status == "ambiguous"
        client.visible = True
        await reconcile_receipt(ack.delivery.id, ctx)
        receipt = await store.get_delivery(ack.delivery.id)
        assert receipt.status == "completed"
        assert receipt.reconciliation_attempts == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_queue_acceptance_crash_restart_never_resends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_pending
    from outfitter.dispatch.core.queue import drain_idle_queues

    path = tmp_path / "registry.db"
    store = await Registry.open(path)
    client = HistoryClient()
    client.lose_ack = False
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        ctx = make_ctx(store, client)

        class SimulatedCrash(BaseException):
            pass

        async def crash_after_acceptance(*args: object, **kwargs: object) -> None:
            raise SimulatedCrash("synthetic process crash before receipt bookkeeping")

        with monkeypatch.context() as patch:
            patch.setattr(store, "apply_receipt_observation", crash_after_acceptance)
            with pytest.raises(SimulatedCrash, match="synthetic process crash"):
                await handlers.send_message(
                    SendInput(lane="target", text="hello", mode="queue", idempotency_key="one"), ctx
                )
        reserved = await store.get_delivery_by_key("one")
        assert reserved is not None and reserved.status == "submitting"
        await store.close()

        store = await Registry.open(path)
        ctx = make_ctx(store, client)
        await store.recover_deliveries()
        await store.mark_lane_idle("target")
        assert await drain_idle_queues(ctx) == 0
        assert (await store.get_delivery(reserved.id)).status == "ambiguous"
        await reconcile_pending(ctx)
        assert (await store.get_delivery(reserved.id)).status == "completed"
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_manual_reconcile_after_exhaustion_never_resubmits() -> None:
    from outfitter.dispatch.core.delivery import get_receipt, reconcile_receipt_request
    from outfitter.dispatch.core.delivery_reconciliation import MAX_CHECKS, reconcile_receipt
    from outfitter.dispatch.core.models import DeliveryLookupInput

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = HistoryClient()
        client.visible = False
        ctx = make_ctx(store, client)
        ack = await handlers.send_message(
            SendInput(lane="target", text="hello", idempotency_key="one"),
            ctx,
        )
        assert ack.delivery is not None
        for _ in range(MAX_CHECKS):
            await reconcile_receipt(ack.delivery.id, ctx)
        lookup = DeliveryLookupInput(receipt_id=ack.delivery.id)
        assert "exhausted" in ((await get_receipt(lookup, ctx)).error or "")
        client.visible = True
        await reconcile_receipt(ack.delivery.id, ctx)
        assert (await get_receipt(lookup, ctx)).status == "ambiguous"
        resolved = await reconcile_receipt_request(lookup, ctx)
        assert resolved.status == "completed"
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
    finally:
        await store.close()


async def test_transient_bookkeeping_error_reconciles_without_daemon_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_receipt

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = HistoryClient()
        client.lose_ack = False
        ctx = make_ctx(store, client)
        original = store.apply_receipt_observation

        async def fail_once(*args: object, **kwargs: object) -> None:
            monkeypatch.setattr(store, "apply_receipt_observation", original)
            raise RuntimeError("synthetic one-shot local bookkeeping failure")

        monkeypatch.setattr(store, "apply_receipt_observation", fail_once)
        with pytest.raises(RuntimeError, match="bookkeeping failure"):
            await handlers.send_message(
                SendInput(lane="target", text="hello", idempotency_key="one"), ctx
            )
        receipt = await store.get_delivery_by_key("one")
        assert receipt is not None and receipt.status == "ambiguous"
        await reconcile_receipt(receipt.id, ctx)
        assert (await store.get_delivery(receipt.id)).status == "completed"
        assert len([c for c in client.calls if c[0] == "turn_start"]) == 1
    finally:
        await store.close()
