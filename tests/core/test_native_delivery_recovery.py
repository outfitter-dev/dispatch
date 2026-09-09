"""Native ambiguity survives restart and resolves only with exact provider facts."""

from pathlib import Path

from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.client.models import ThreadTurn, ThreadTurnsPage
from outfitter.dispatch.client.native_queue import QueuedSubmission
from outfitter.dispatch.config import RuntimePolicy
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.delivery_reconciliation import reconcile_pending, reconcile_receipt
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.registry.store import Registry
from tests.core.test_native_delivery import NativeClient
from tests.fakes import make_ctx


class LostAck(NativeClient):
    async def thread_queue_add(
        self, thread_id: str, text: str, *, client_user_message_id: str
    ) -> QueuedSubmission:
        await super().thread_queue_add(
            thread_id, text, client_user_message_id=client_user_message_id
        )
        raise TransportError("synthetic lost ACK")


async def test_unknown_native_arrival_remains_held_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    store = await Registry.open(path)
    await store.add_lane(id="target", handle="@target", source="attached", status="busy")
    client = LostAck()
    policy = RuntimePolicy(allow_attached_writes=True)
    inp = SendInput(lane="target", mode="queue", text="hello", idempotency_key="key")
    first = await handlers.send_message(inp, make_ctx(store, client, policy=policy))
    assert first.delivery and first.delivery.status == "ambiguous"
    await store.close()
    store = await Registry.open(path)
    try:
        await store.recover_deliveries()
        ctx = make_ctx(store, client, policy=policy)
        for _ in range(5):
            await reconcile_pending(ctx)
        replay = await handlers.send_message(inp, ctx)
        assert replay.delivery and replay.delivery.id == first.delivery.id
        assert replay.delivery.status == "ambiguous"
        assert replay.delivery.reconciliation_attempts == 3
        assert [name for name, _ in client.calls].count("thread_queue_add") == 1
        assert await store.lane_delivery_held("target")
    finally:
        await store.close()


async def test_native_history_correlates_completion_after_queue_disappears() -> None:
    class History(LostAck):
        async def thread_turns_list(self, thread_id: str, **kwargs: object) -> ThreadTurnsPage:
            call = next(payload for name, payload in self.calls if name == "thread_queue_add")
            return ThreadTurnsPage(
                data=[
                    ThreadTurn(
                        id="turn-1",
                        status="completed",
                        items=[
                            {
                                "type": "userMessage",
                                "id": "item-1",
                                "clientId": call["client_user_message_id"],
                                "content": [{"type": "text", "text": call["text"]}],
                            }
                        ],
                    )
                ]
            )

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="attached", status="busy")
        client = History()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        first = await handlers.send_message(
            SendInput(lane="target", mode="queue", text="hello"), ctx
        )
        assert first.delivery and first.delivery.status == "ambiguous"
        await reconcile_receipt(first.delivery.id, ctx)
        resolved = await store.get_delivery(first.delivery.id)
        assert resolved.status == "completed" and resolved.turn_id == "turn-1"
        assert resolved.submission_id is None
        before = list(client.calls)
        await reconcile_receipt(first.delivery.id, ctx)
        assert client.calls == before
        assert (await store.get_lane("target")).status == "busy"
    finally:
        await store.close()
