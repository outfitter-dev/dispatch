"""Native attached delivery keeps the external writer and durable receipt."""

from outfitter.dispatch.client.models import ThreadInfo
from outfitter.dispatch.client.native_queue import QueuedSubmission
from outfitter.dispatch.config import RuntimePolicy
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx


class NativeClient(FakeLaneClient):
    async def thread_queue_add(
        self, thread_id: str, text: str, *, client_user_message_id: str
    ) -> QueuedSubmission:
        self._record(
            "thread_queue_add",
            thread_id=thread_id,
            text=text,
            client_user_message_id=client_user_message_id,
        )
        return QueuedSubmission.model_validate(
            {
                "id": "submission-1",
                "clientUserMessageId": client_user_message_id,
                "input": [{"type": "text", "text": text}],
            }
        )


async def test_busy_attached_queue_admitted_once_without_claiming_writer() -> None:
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="attached", status="busy")
        client = NativeClient()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        inp = SendInput(lane="target", text="hello", mode="queue", idempotency_key="event-1")
        first = await handlers.send_message(inp, ctx)
        replay = await handlers.send_message(inp, ctx)
        assert first.delivery is not None and replay.delivery is not None
        assert first.delivery.id == replay.delivery.id
        assert first.delivery.status == "accepted"
        assert first.delivery.submission_id == "submission-1"
        assert first.delivery.transport == "native_queue"
        assert first.delivery.turn_id is None
        assert [name for name, _ in client.calls] == ["thread_queue_add"]
        assert (await store.get_lane("target")).status == "busy"
    finally:
        await store.close()


async def test_first_contact_native_queue_uses_metadata_without_history_resume() -> None:
    store = await Registry.open()
    try:
        client = NativeClient()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        target = "11111111-1111-4111-8111-111111111111"
        result = await handlers.send_message(
            SendInput(lane=target, text="hello", mode="queue", idempotency_key="first"), ctx
        )
        assert result.delivery and result.delivery.status == "accepted"
        assert (await store.get_lane(target)).source == "attached"
        assert [name for name, _ in client.calls] == ["thread_read", "thread_queue_add"]
    finally:
        await store.close()


async def test_native_lost_ack_reconciles_exact_queue_entry_without_resending() -> None:
    from outfitter.dispatch.client.errors import TransportError
    from outfitter.dispatch.client.native_queue import ThreadQueuePage
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_receipt

    class LostAck(NativeClient):
        saved: QueuedSubmission | None = None

        async def thread_queue_add(
            self, thread_id: str, text: str, *, client_user_message_id: str
        ) -> QueuedSubmission:
            self.saved = await super().thread_queue_add(
                thread_id, text, client_user_message_id=client_user_message_id
            )
            raise TransportError("lost ACK")

        async def thread_queue_list(
            self, thread_id: str, *, cursor: str | None = None, limit: int | None = None
        ) -> ThreadQueuePage:
            assert self.saved is not None
            return ThreadQueuePage(data=[self.saved])

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="attached", status="busy")
        client = LostAck()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        inp = SendInput(lane="target", text="hello", mode="queue", idempotency_key="event-1")
        first = await handlers.send_message(inp, ctx)
        assert first.delivery is not None and first.delivery.status == "ambiguous"
        await reconcile_receipt(first.delivery.id, ctx)
        replay = await handlers.send_message(inp, ctx)
        assert replay.delivery is not None and replay.delivery.status == "accepted"
        assert replay.delivery.submission_id == "submission-1"
        assert replay.delivery.turn_id is None
        assert [name for name, _ in client.calls].count("thread_queue_add") == 1
    finally:
        await store.close()


async def test_held_native_delivery_drains_while_owner_is_busy() -> None:
    from outfitter.dispatch.core.delivery import send_reserved
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_pending

    store = await Registry.open()
    try:
        lane = await store.add_lane(id="target", handle="@target", source="attached", status="busy")
        client = NativeClient()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        first, _ = await store.reserve_delivery(
            key="first",
            lane="target",
            mode="queue",
            payload='{"text":"first"}',
            text="first",
            transport="native_queue",
        )
        await store.claim_delivery(first.id)
        held = await send_reserved(
            SendInput(lane="target", mode="queue", text="next"), lane, "next", ctx
        )
        assert held.status == "queued"
        await store.update_delivery(first.id, status="accepted", submission_id="first-provider")
        await reconcile_pending(ctx)
        assert (await store.get_delivery(held.id)).status == "accepted"
        assert [name for name, _ in client.calls] == ["thread_queue_add"]
        assert (await store.get_lane("target")).status == "busy"
    finally:
        await store.close()


async def test_unsupported_native_queue_is_explicit_and_never_falls_back() -> None:
    import pytest

    from outfitter.dispatch.client.errors import AppServerError
    from outfitter.dispatch.contracts.errors import DispatchError

    class Unsupported(NativeClient):
        async def thread_queue_add(
            self, thread_id: str, text: str, *, client_user_message_id: str
        ) -> QueuedSubmission:
            self._record("thread_queue_add")
            raise AppServerError(-32601, "method not found")

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="attached", status="idle")
        client = Unsupported()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        with pytest.raises(DispatchError, match="native queue") as caught:
            await handlers.send_message(
                SendInput(lane="target", mode="queue", text="hi", idempotency_key="key"), ctx
            )
        assert caught.value.code == "capability_unavailable"
        receipt = await store.get_delivery_by_key("key")
        assert receipt is not None and receipt.status == "failed"
        assert [name for name, _ in client.calls] == ["thread_queue_add"]
        assert (await store.get_lane("target")).status == "idle"
    finally:
        await store.close()


async def test_policy_revocation_prevents_held_native_submission() -> None:
    from outfitter.dispatch.core.delivery_reconciliation import reconcile_pending

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="attached", status="busy")
        receipt, _ = await store.reserve_delivery(
            key="key",
            lane="target",
            mode="queue",
            payload='{"text":"held"}',
            text="held",
            transport="native_queue",
        )
        client = NativeClient()
        await reconcile_pending(make_ctx(store, client))
        assert client.calls == []
        assert (await store.get_delivery(receipt.id)).status == "failed"
    finally:
        await store.close()


async def test_native_concurrent_callers_share_one_receipt_and_submission() -> None:
    import asyncio

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="attached", status="idle")
        client = NativeClient()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        inp = SendInput(lane="target", mode="queue", text="same", idempotency_key="key")
        results = await asyncio.gather(*(handlers.send_message(inp, ctx) for _ in range(5)))
        assert len({result.delivery.id for result in results if result.delivery}) == 1
        assert [name for name, _ in client.calls] == ["thread_queue_add"]
    finally:
        await store.close()


async def test_unkeyed_native_requests_still_have_distinct_durable_receipts() -> None:
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="attached", status="idle")
        ctx = make_ctx(store, NativeClient(), policy=RuntimePolicy(allow_attached_writes=True))
        inp = SendInput(lane="target", mode="queue", text="same")
        first = await handlers.send_message(inp, ctx)
        second = await handlers.send_message(inp, ctx)
        assert first.delivery and second.delivery
        assert first.delivery.id != second.delivery.id
        assert first.delivery.key is None
        assert (await store.get_delivery(first.delivery.id)).submission_id == "submission-1"
    finally:
        await store.close()


async def test_app_writer_rejection_does_not_fake_steer_or_queue() -> None:
    import pytest

    from outfitter.dispatch.client.errors import AppServerError
    from outfitter.dispatch.contracts.errors import DispatchError

    class AppWriter(NativeClient):
        async def thread_resume(self, thread_id: str, **kwargs: object) -> ThreadInfo:
            self._record("thread_resume", thread_id=thread_id)
            raise AppServerError(-32600, f"thread {thread_id} already has an active writer")

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="attached", status="busy")
        await store.record_turn_started("target", "active-turn")
        client = AppWriter()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        with pytest.raises(DispatchError, match="native queue") as error:
            await handlers.send_message(SendInput(lane="target", mode="steer", text="change"), ctx)
        assert error.value.code == "capability_unavailable"
        assert [name for name, _ in client.calls] == ["thread_resume"]
    finally:
        await store.close()
