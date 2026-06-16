"""Scheduler (time triggers), reactor (event triggers + state), and guards."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio

from outfitter.dispatch.client.events import ApprovalRequested, LaneIdle, TurnCompleted, TurnStarted
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.scheduler import Scheduler
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.models import (
    BriefAction,
    CronWhen,
    EventWhen,
    Guard,
    IdleForWhen,
    IntervalWhen,
    SendAction,
    Subscription,
    Trigger,
)
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeClock, FakeLaneClient, make_ctx

_T0 = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open(now=lambda: _T0)
    try:
        yield s
    finally:
        await s.close()


def _fired_send(client: FakeLaneClient) -> bool:
    return any(name == "turn_start" for name, _ in client.calls)


async def test_interval_fires_then_waits_for_the_window(store: Registry) -> None:
    clock = FakeClock(_T0)
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")
    await store.add_trigger(
        Trigger(
            id="t1",
            name="p",
            lane="L1",
            when=IntervalWhen(seconds=60),
            action=SendAction(text="ping"),
        )
    )
    scheduler = Scheduler(ctx, TriggerRunner(ctx, clock), clock)

    assert await scheduler.tick() == 1  # first tick fires
    assert _fired_send(client)
    clock.advance(30)
    assert await scheduler.tick() == 0  # within the window
    clock.advance(31)
    assert await scheduler.tick() == 1  # 61s since last fire


async def test_trigger_runner_resolves_dispatch_ref(store: Registry) -> None:
    clock = FakeClock(_T0)
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    lane = await store.add_lane(id="L1", handle="@x", source="own", status="idle")
    trig = Trigger(
        id="t1",
        name="p",
        lane=lane.ref,
        when=IntervalWhen(seconds=1),
        action=SendAction(text="ping"),
    )
    await store.add_trigger(trig)

    assert await TriggerRunner(ctx, clock).maybe_fire(trig, reason="time") is True
    assert any(name == "turn_start" and kw["thread_id"] == "L1" for name, kw in client.calls)


async def test_idle_for_fires_once_per_idle_period(store: Registry) -> None:
    clock = FakeClock(_T0)
    ctx = make_ctx(store)
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")
    await store.touch_lane_event("L1", when=_T0)  # idle since T0
    await store.add_trigger(
        Trigger(
            id="t1",
            name="p",
            lane="L1",
            when=IdleForWhen(seconds=10),
            action=SendAction(text="still there?"),
        )
    )
    scheduler = Scheduler(ctx, TriggerRunner(ctx, clock), clock)

    clock.advance(5)
    assert await scheduler.tick() == 0  # not idle long enough
    clock.advance(6)  # now 11s idle
    assert await scheduler.tick() == 1
    clock.advance(20)
    assert await scheduler.tick() == 0  # already fired this idle period


async def test_cron_fires_at_next_minute_not_before(store: Registry) -> None:
    clock = FakeClock(_T0)  # T0 is on a minute boundary (12:00:00)
    ctx = make_ctx(store)
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")
    await store.add_trigger(
        Trigger(
            id="t1",
            name="p",
            lane="L1",
            when=CronWhen(expr="* * * * *"),
            action=SendAction(text="tick"),
        )
    )
    scheduler = Scheduler(ctx, TriggerRunner(ctx, clock), clock)
    clock.advance(30)  # still within the same minute (base=now → next is the next minute)
    assert await scheduler.tick() == 0
    clock.advance(31)  # now past the minute boundary
    assert await scheduler.tick() == 1


async def test_min_interval_and_dedupe_guards(store: Registry) -> None:
    clock = FakeClock(_T0)
    ctx = make_ctx(store)
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")
    runner = TriggerRunner(ctx, clock)
    trig = Trigger(
        id="t1",
        name="p",
        lane="L1",
        when=IntervalWhen(seconds=1),
        action=SendAction(text="same"),
        guard=Guard(min_interval=100, dedupe=True),
    )
    await store.add_trigger(trig)
    assert await runner.maybe_fire(trig, reason="time") is True
    refreshed = await store.get_trigger("t1")
    clock.advance(5)
    assert await runner.maybe_fire(refreshed, reason="time") is False  # min_interval


async def test_idle_only_guard_skips_busy_lane(store: Registry) -> None:
    clock = FakeClock(_T0)
    ctx = make_ctx(store)
    await store.add_lane(id="L1", handle="@x", source="own", status="busy")
    runner = TriggerRunner(ctx, clock)
    trig = Trigger(
        id="t1",
        name="p",
        lane="L1",
        when=IntervalWhen(seconds=1),
        action=SendAction(text="x"),
        guard=Guard(idle_only=True),
    )
    assert await runner.maybe_fire(trig, reason="time") is False


async def test_reactor_turn_lifecycle_updates_lane_state(store: Registry) -> None:
    ctx = make_ctx(store)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")

    await reactor.handle(TurnStarted("L1", "turn-1"))
    lane = await store.get_lane("L1")
    assert lane.status == "busy"
    assert lane.active_turn_id == "turn-1"

    await reactor.handle(TurnCompleted("L1", "turn-1"))
    lane = await store.get_lane("L1")
    assert lane.status == "idle"
    assert lane.active_turn_id is None


async def test_reactor_drains_one_queued_message_on_turn_completed(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="busy")
    await store.enqueue_message(lane="L1", text="first")
    await store.enqueue_message(lane="L1", text="second")

    await reactor.handle(TurnCompleted("L1", "turn-1"))

    assert (await store.get_queued_message(1)).status == "sent"
    assert (await store.get_queued_message(2)).status == "pending"
    assert (await store.get_lane("L1")).status == "busy"
    sent = [kw["text"] for name, kw in client.calls if name == "turn_start"]
    assert sent == ["first"]


async def test_reactor_drains_queued_message_on_lane_idle_event(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="busy")
    await store.enqueue_message(lane="L1", text="queued")

    await reactor.handle(LaneIdle("L1"))

    assert (await store.get_queued_message(1)).status == "sent"
    assert any(name == "turn_start" and kw["text"] == "queued" for name, kw in client.calls)


async def test_reactor_fires_turn_completed_trigger_and_audits(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="busy")
    await store.add_trigger(
        Trigger(
            id="t1",
            name="p",
            lane="L1",
            when=EventWhen(event="turn_completed"),
            action=SendAction(text="next?"),
        )
    )
    await reactor.handle(TurnCompleted("L1", "turn-1"))
    assert _fired_send(client)
    recent = await store.recent_actions()
    assert any(r.trigger_id == "t1" for r in recent)


async def test_reactor_delivers_done_subscription_to_inbox_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="target", handle="@target", source="own", status="busy")
    await store.add_lane(id="subscriber", handle="@subscriber", source="own", status="idle")
    await store.add_subscription(
        Subscription(
            id="sub_1",
            target_lane="target",
            subscriber_lane="subscriber",
            when="done",
            delivery="turn",
            deliver="idle",
            tail=0,
            once=True,
            ack="auto",
            created_at=_T0,
            updated_at=_T0,
        )
    )

    await reactor.handle(TurnCompleted("target", "turn-1"))

    messages = await store.list_inbox_messages(lane="subscriber", state=None)
    assert len(messages) == 1
    assert messages[0].state == "acked"
    assert messages[0].queued_message_id == 1
    assert (await store.get_subscription("sub_1")).state == "done"
    sent = [kw for name, kw in client.calls if name == "turn_start"]
    assert len(sent) == 1
    assert sent[0]["thread_id"] == "subscriber"
    text = sent[0]["text"]
    assert isinstance(text, str)
    assert "Event: completed" in text


async def test_reactor_auto_declines_unhandled_approval(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="busy")
    await reactor.handle(ApprovalRequested("L1", 7, "command"))
    assert any(
        name == "respond_approval" and kw["decision"] == "decline" for name, kw in client.calls
    )


async def test_reactor_approval_trigger_suppresses_auto_decline(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="busy")
    await store.add_trigger(
        Trigger(
            id="t1",
            name="ping",
            lane="L1",
            when=EventWhen(event="waiting_on_approval"),
            action=BriefAction(text="approval pending on @x"),
        )
    )
    await reactor.handle(ApprovalRequested("L1", 7, "command"))
    assert not any(name == "respond_approval" for name, _ in client.calls)
    assert any(name == "inject_items" for name, _ in client.calls)


async def test_reactor_approval_subscription_suppresses_auto_decline(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="target", handle="@target", source="own", status="busy")
    await store.add_lane(id="subscriber", handle="@subscriber", source="own", status="idle")
    await store.add_subscription(
        Subscription(
            id="sub_approval",
            target_lane="target",
            subscriber_lane="subscriber",
            when="approval",
            delivery="inbox",
            deliver="idle",
            tail=0,
            once=False,
            ack="manual",
            created_at=_T0,
            updated_at=_T0,
        )
    )

    await reactor.handle(ApprovalRequested("target", 7, "command"))

    assert not any(name == "respond_approval" for name, _ in client.calls)
    messages = await store.list_inbox_messages(lane="subscriber")
    assert len(messages) == 1
    assert messages[0].payload["event"] == "approval"


async def test_reactor_guard_suppressed_approval_trigger_still_suppresses_decline(
    store: Registry,
) -> None:
    # A registered handler whose guard suppresses this firing must NOT be overridden
    # by an auto-decline (the user registered a handler).
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="busy")
    await store.add_trigger(
        Trigger(
            id="t1",
            name="ping",
            lane="L1",
            when=EventWhen(event="waiting_on_approval"),
            action=BriefAction(text="pending"),
            guard=Guard(dedupe=True),
        )
    )
    await reactor.handle(ApprovalRequested("L1", 1, "command"))  # fires
    await reactor.handle(ApprovalRequested("L1", 2, "command"))  # dedupe-suppressed
    declines = [kw for name, kw in client.calls if name == "respond_approval"]
    assert declines == []  # never auto-declined despite the second firing being suppressed
