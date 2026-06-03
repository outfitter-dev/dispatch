"""Unit tests for the router/broadcaster demux behavior."""

from __future__ import annotations

import asyncio

from outfitter.dispatch.client.events import ApprovalRequested, TurnStarted
from outfitter.dispatch.client.router import Broadcaster, Router


async def test_broadcaster_fans_out_to_lane_and_global_subscribers() -> None:
    bus: Broadcaster[str] = Broadcaster()
    lane_sub = bus.subscribe("L1")
    global_sub = bus.subscribe(None)
    other_sub = bus.subscribe("L2")
    bus.publish("L1", "hello")
    assert await asyncio.wait_for(lane_sub.__anext__(), timeout=1) == "hello"
    assert await asyncio.wait_for(global_sub.__anext__(), timeout=1) == "hello"
    # L2 subscriber sees nothing; closing ends its iteration cleanly.
    bus.close()
    try:
        await asyncio.wait_for(other_sub.__anext__(), timeout=1)
        raise AssertionError("expected StopAsyncIteration")
    except StopAsyncIteration:
        pass


async def test_router_routes_notification_to_lane_events_and_raw() -> None:
    router = Router()
    events = router.events.subscribe("L1")
    raw = router.raw.subscribe("L1")
    router.handle({"method": "turn/started", "params": {"threadId": "L1", "turnId": "T1"}})
    assert await asyncio.wait_for(events.__anext__(), timeout=1) == TurnStarted("L1", "T1")
    raw_msg = await asyncio.wait_for(raw.__anext__(), timeout=1)
    assert raw_msg["method"] == "turn/started"


async def test_discard_request_drops_pending_and_ignores_late_response() -> None:
    router = Router()
    fut = router.new_request(7)
    assert 7 in router._pending
    router.discard_request(7)  # the awaiter was cancelled (bounded timeout)
    assert 7 not in router._pending
    # A late response for the abandoned id is a harmless no-op (does not crash).
    router.handle({"id": 7, "result": {"thread": {"id": "x"}}})
    assert not fut.done()


async def test_router_routes_server_request_to_approval_event() -> None:
    router = Router()
    events = router.events.subscribe("L1")
    router.handle(
        {
            "id": 99,
            "method": "item/fileChange/requestApproval",
            "params": {"threadId": "L1", "itemId": "I1"},
        }
    )
    event = await asyncio.wait_for(events.__anext__(), timeout=1)
    assert event == ApprovalRequested("L1", 99, "file_change", "I1", None)
