"""Unit tests for the router/broadcaster demux behavior."""

from __future__ import annotations

import asyncio

from outfitter.dispatch.client.events import ApprovalRequested, ServerRequestReceived, TurnStarted
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


async def test_router_resolves_string_response_ids() -> None:
    router = Router()
    fut = router.new_request("request-7")
    router.handle({"id": "request-7", "result": {"ok": True}})
    assert await asyncio.wait_for(fut, timeout=1) == {"ok": True}


async def test_router_routes_server_request_to_approval_event() -> None:
    router = Router()
    events = router.events.subscribe("L1")
    requests = router.requests.subscribe("L1")
    all_requests = router.requests.subscribe(None)
    router.handle(
        {
            "id": 99,
            "method": "item/fileChange/requestApproval",
            "params": {"threadId": "L1", "itemId": "I1"},
        }
    )
    event = await asyncio.wait_for(events.__anext__(), timeout=1)
    assert event == ApprovalRequested("L1", 99, "file_change", "I1", None)
    expected = ServerRequestReceived(
        method="item/fileChange/requestApproval",
        request_id=99,
        category="approval",
        thread_id="L1",
        item_id="I1",
    )
    assert await asyncio.wait_for(requests.__anext__(), timeout=1) == expected
    assert await asyncio.wait_for(all_requests.__anext__(), timeout=1) == expected


async def test_router_routes_threadless_and_legacy_server_requests_to_generic_streams() -> None:
    router = Router()
    all_requests = router.requests.subscribe(None)
    legacy_requests = router.requests.subscribe("legacy-1")

    router.handle(
        {
            "id": "auth-1",
            "method": "account/chatgptAuthTokens/refresh",
            "params": {"credential": "not logged"},
        }
    )
    auth = await asyncio.wait_for(all_requests.__anext__(), timeout=1)
    assert auth.request_id == "auth-1"
    assert auth.category == "auth"
    assert auth.lane_id is None

    router.handle(
        {
            "id": "attestation-1",
            "method": "attestation/generate",
            "params": {"conversationId": "legacy-1"},
        }
    )
    expected = ServerRequestReceived(
        method="attestation/generate",
        request_id="attestation-1",
        category="attestation",
        conversation_id="legacy-1",
    )
    assert await asyncio.wait_for(legacy_requests.__anext__(), timeout=1) == expected
    assert await asyncio.wait_for(all_requests.__anext__(), timeout=1) == expected


async def test_router_publishes_unknown_server_requests() -> None:
    router = Router()
    requests = router.requests.subscribe(None)
    router.handle({"id": "future-1", "method": "future/request", "params": {}})
    request = await asyncio.wait_for(requests.__anext__(), timeout=1)
    assert request == ServerRequestReceived(
        method="future/request", request_id="future-1", category="unknown"
    )
