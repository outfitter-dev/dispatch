"""Captured Codex lifecycle notifications must support active-turn operations."""

from __future__ import annotations

from datetime import UTC, datetime

from outfitter.dispatch.client.events import project_notification
from outfitter.dispatch.core.handlers import send_message
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
