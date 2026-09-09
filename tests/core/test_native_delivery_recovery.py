"""Native ambiguity survives restart and resolves only with exact provider facts."""

import asyncio
from pathlib import Path

import pytest

from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.client.models import ThreadTurn, ThreadTurnsPage
from outfitter.dispatch.client.native_queue import QueuedSubmission, ThreadQueuePage
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


@pytest.mark.parametrize(
    "queue_failure",
    [
        None,
        "transport",
        "timeout",
        "stall",
        "page_budget",
        "conflict",
        "duplicate",
        "cancel",
        "partial_transport",
        "partial_stall",
        "partial_budget",
    ],
)
@pytest.mark.parametrize("acknowledged", [False, True])
async def test_native_history_correlates_completion_after_queue_disappears(
    queue_failure: str | None,
    acknowledged: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("outfitter.dispatch.core.native_queue_evidence.NATIVE_QUEUE_TIMEOUT", 0.01)

    class History(LostAck):
        history_reads = 0

        async def thread_queue_list(
            self, thread_id: str, *, cursor: str | None = None, limit: int | None = None
        ) -> ThreadQueuePage:
            if queue_failure and queue_failure.startswith("partial_"):
                call = next(payload for name, payload in self.calls if name == "thread_queue_add")
                if cursor is None:
                    entry = QueuedSubmission.model_validate(
                        {
                            "id": "submission-1",
                            "clientUserMessageId": call["client_user_message_id"],
                            "input": [{"type": "text", "text": call["text"]}],
                        }
                    )
                    return ThreadQueuePage(data=[entry], next_cursor="1")
                if queue_failure == "partial_transport":
                    raise TransportError("partial queue failure")
                if queue_failure == "partial_stall":
                    await asyncio.Event().wait()
                return ThreadQueuePage(data=[], next_cursor=str(int(cursor) + 1))
            if queue_failure == "transport":
                raise TransportError("queue unavailable")
            if queue_failure == "timeout":
                raise TimeoutError("queue timeout")
            if queue_failure == "stall":
                await asyncio.Event().wait()
            if queue_failure == "cancel":
                raise asyncio.CancelledError
            if queue_failure in {"conflict", "duplicate"}:
                call = next(payload for name, payload in self.calls if name == "thread_queue_add")
                entry = QueuedSubmission.model_validate(
                    {
                        "id": "submission-1",
                        "clientUserMessageId": call["client_user_message_id"],
                        "input": [
                            {
                                "type": "text",
                                "text": "wrong" if queue_failure == "conflict" else call["text"],
                            }
                        ],
                    }
                )
                return ThreadQueuePage(
                    data=[entry] if queue_failure == "conflict" else [entry, entry]
                )
            if queue_failure == "page_budget":
                return ThreadQueuePage(data=[], next_cursor=str(int(cursor or "0") + 1))
            return ThreadQueuePage(data=[])

        async def thread_turns_list(self, thread_id: str, **kwargs: object) -> ThreadTurnsPage:
            self.history_reads += 1
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
        if acknowledged:
            await store.update_delivery(first.delivery.id, status="accepted")
        if queue_failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await reconcile_receipt(first.delivery.id, ctx, automatic=False)
            assert client.history_reads == 0
            return
        await reconcile_receipt(first.delivery.id, ctx, automatic=False)
        if queue_failure in {
            "conflict",
            "duplicate",
            "partial_transport",
            "partial_stall",
            "partial_budget",
        }:
            held = await store.get_delivery(first.delivery.id)
            assert held.status == ("accepted" if acknowledged else "ambiguous")
            assert held.turn_id is None and client.history_reads == 0
            return
        assert client.history_reads == 1
        resolved = await store.get_delivery(first.delivery.id)
        assert resolved.status == "completed" and resolved.turn_id == "turn-1"
        assert resolved.submission_id is None
        before = list(client.calls)
        await reconcile_receipt(first.delivery.id, ctx)
        assert client.calls == before
        assert (await store.get_lane("target")).status == "busy"
    finally:
        await store.close()
