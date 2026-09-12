"""Captured Codex lifecycle notifications must support active-turn operations."""

from __future__ import annotations

from datetime import UTC, datetime

from outfitter.dispatch.client.events import project_notification
from outfitter.dispatch.core.handlers import send_message
from outfitter.dispatch.core.history_index import index_codex_thread_read
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx
from tests.fixtures import load_jsonl


async def test_captured_lifecycle_turn_id_enables_steering() -> None:
    rows = load_jsonl("app_server", "events", "turn_lifecycle_v0153.jsonl")
    row = rows[0]
    method, params = row["method"], row["params"]
    assert isinstance(method, str) and isinstance(params, dict)
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        await store.add_lane(id="lab-thread-153", handle="@lab", source="own", status="idle")
        reactor = Reactor(ctx, TriggerRunner(ctx, lambda: datetime.now(UTC)))
        for event in project_notification(method, params):
            await reactor.handle(event)

        ack = await send_message(
            SendInput(lane="lab-thread-153", text="finish with the revised token", mode="steer"),
            ctx,
        )

        assert ack.accepted
        steer_calls = [call for name, call in client.calls if name == "turn_steer"]
        assert len(steer_calls) == 1
        assert steer_calls[0]["expected_turn_id"] == "lab-turn-153"
        assert not any(name == "turn_start" for name, _ in client.calls)

        completed = rows[1]
        method, params = completed["method"], completed["params"]
        assert isinstance(method, str) and isinstance(params, dict)
        for event in project_notification(method, params):
            await reactor.handle(event)
        lane = await store.get_lane("lab-thread-153")
        assert lane.active_turn_id is None
        assert lane.latest_turn_id == "lab-turn-153"
        assert lane.latest_turn_status == "completed"
    finally:
        await store.close()


async def test_nested_failed_completion_preserves_error_and_never_succeeds() -> None:
    await _assert_terminal_failure("failed", "provider failed")


async def test_nested_interrupted_completion_never_succeeds() -> None:
    await _assert_terminal_failure("interrupted", "turn interrupted")


async def _assert_terminal_failure(status: str, expected_error: str) -> None:
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        await store.add_lane(id="target", handle="@target", source="own", status="busy")
        await store.record_turn_started("target", "turn-1")
        await store.enqueue_message(lane="target", text="must not start on unsuccessful completion")
        reactor = Reactor(ctx, TriggerRunner(ctx, lambda: datetime.now(UTC)))
        params: dict[str, object] = {
            "threadId": "target",
            "turn": {
                "id": "turn-1",
                "status": status,
                "error": {"message": expected_error} if status == "failed" else None,
            },
        }
        for event in project_notification("turn/completed", params):
            await reactor.handle(event)
        lane = await store.get_lane("target")
        turn = await store.get_thread_turn("codex", "target", "turn-1")
        assert lane.latest_turn_status == status
        assert lane.latest_error == expected_error
        assert lane.active_turn_id is None
        assert turn.status == status and turn.error == expected_error
        assert turn.failed_at is not None
        [indexed_event] = await store.list_provider_events(lane="target")
        assert indexed_event.summary["status"] == status
        assert not any(name == "turn_start" for name, _ in client.calls)
    finally:
        await store.close()


async def test_interrupted_history_preserves_terminal_timestamp() -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(id="target", handle="@target", source="own")
        await index_codex_thread_read(
            store,
            lane,
            {
                "thread": {
                    "id": "target",
                    "turns": [{"id": "turn-1", "status": "interrupted", "items": []}],
                }
            },
        )
        turn = await store.get_thread_turn("codex", "target", "turn-1")
        assert turn.status == "interrupted"
        assert turn.failed_at is not None
        assert turn.completed_at is None
    finally:
        await store.close()


async def test_nonterminal_nested_status_is_not_a_successful_completion() -> None:
    assert (
        project_notification(
            "turn/completed",
            {"threadId": "target", "turn": {"id": "turn-1", "status": "inProgress"}},
        )
        == []
    )


async def test_old_terminal_settles_its_receipt_without_ending_newer_turn() -> None:
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        await store.add_lane(id="target", handle="@target", source="own", status="busy")
        receipt, _ = await store.reserve_delivery(
            delivery_id="receipt-old",
            key=None,
            lane="target",
            mode="send",
            payload='{"text":"old"}',
            text="old",
            correlation_id="receipt-old",
        )
        await store.update_delivery(receipt.id, status="accepted", turn_id="turn-old")
        await store.record_turn_started("target", "turn-new")
        queued = await store.enqueue_message(lane="target", text="later")
        reactor = Reactor(ctx, TriggerRunner(ctx, lambda: datetime.now(UTC)))

        for event in project_notification(
            "turn/completed",
            {"threadId": "target", "turn": {"id": "turn-old", "status": "completed"}},
        ):
            await reactor.handle(event)

        lane = await store.get_lane("target")
        settled = await store.get_delivery(receipt.id)
        assert settled.status == "completed"
        assert lane.status == "busy"
        assert lane.active_turn_id == "turn-new"
        assert (await store.get_queued_message(queued.id)).status == "pending"
    finally:
        await store.close()
