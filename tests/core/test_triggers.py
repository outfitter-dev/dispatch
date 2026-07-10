"""Scheduler (time triggers), reactor (event triggers + state), and guards."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio

from outfitter.dispatch.client.events import (
    ApprovalRequested,
    LaneIdle,
    ServerRequestReceived,
    ThreadArchived,
    ThreadUnarchived,
    TokenUsageUpdated,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from outfitter.dispatch.config import CapturePolicy
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
    events = list(reversed(await store.list_provider_events(lane="L1")))
    assert [event.event_type for event in events] == ["turn/started", "turn/completed"]
    turns = await store.list_thread_turns(lane="L1")
    assert len(turns) == 1
    assert turns[0].turn_id == "turn-1"
    assert turns[0].status == "completed"
    assert turns[0].started_at is not None
    assert turns[0].completed_at is not None
    runtime = await store.get_lane_runtime_state("L1")
    assert runtime is not None
    assert runtime.status == "idle"
    assert runtime.latest_turn_id == "turn-1"
    assert runtime.latest_turn_status == "completed"


async def test_reactor_indexes_bounded_standard_event_summaries(store: Registry) -> None:
    ctx = make_ctx(store, capture=CapturePolicy(max_text_bytes=8))
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")

    await reactor.handle(TurnFailed("L1", "turn-1", "failure message that is too long"))

    [event] = await store.list_provider_events(lane="L1")
    assert event.event_type == "turn/failed"
    assert event.provider_turn_id == "turn-1"
    assert event.payload is None
    assert event.raw_retained is False
    assert event.summary == {
        "status": "failed",
        "turn_id": "turn-1",
        "message": "failure ",
        "message_original_bytes": 32,
        "message_truncated": True,
    }
    [turn] = await store.list_thread_turns(lane="L1")
    assert turn.error == "failure "
    lane = await store.get_lane("L1")
    assert lane.latest_error == "failure "
    runtime = await store.get_lane_runtime_state("L1")
    assert runtime is not None
    assert runtime.attention_detail == "failure "


async def test_reactor_retains_bounded_raw_provider_event_payload_in_debug(
    store: Registry,
) -> None:
    ctx = make_ctx(
        store,
        capture=CapturePolicy(mode="debug", raw_payload_retention="debug", max_payload_bytes=80),
    )
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")

    await reactor.handle(
        TurnStarted(
            "L1",
            "turn-1",
            raw_payload={
                "method": "turn/started",
                "params": {"threadId": "L1", "turnId": "turn-1", "blob": "x" * 400},
            },
        )
    )

    [event] = await store.list_provider_events(lane="L1")
    assert event.raw_retained is True
    assert event.payload is not None
    assert event.payload["truncated"] is True
    async with store._conn.execute(
        "SELECT length(payload) AS bytes FROM provider_events WHERE id = ?",
        (event.id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["bytes"] <= ctx.capture.max_payload_bytes


async def test_reactor_retains_error_raw_provider_event_payload_for_errors_policy(
    store: Registry,
) -> None:
    ctx = make_ctx(
        store,
        capture=CapturePolicy(raw_payload_retention="errors", max_payload_bytes=256),
    )
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")

    await reactor.handle(
        TurnFailed(
            "L1",
            "turn-1",
            "provider failed",
            raw_payload={
                "method": "turn/failed",
                "params": {"threadId": "L1", "turnId": "turn-1", "message": "provider failed"},
            },
        )
    )

    [event] = await store.list_provider_events(lane="L1")
    assert event.raw_retained is True
    assert event.payload is not None
    assert event.payload["method"] == "turn/failed"


async def test_reactor_does_not_retain_non_error_raw_payload_for_errors_policy(
    store: Registry,
) -> None:
    ctx = make_ctx(store, capture=CapturePolicy(raw_payload_retention="errors"))
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")

    await reactor.handle(
        TurnStarted("L1", "turn-1", raw_payload={"method": "turn/started", "params": {}})
    )

    [event] = await store.list_provider_events(lane="L1")
    assert event.payload is None
    assert event.raw_retained is False


async def test_reactor_dedupes_stable_live_events_on_replay(store: Registry) -> None:
    ctx = make_ctx(store)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")

    await reactor.handle(TurnStarted("L1", "turn-1"))
    await reactor.handle(TurnCompleted("L1", "turn-1"))
    await reactor.handle(TurnStarted("L1", "turn-1"))
    await reactor.handle(TurnCompleted("L1", "turn-1"))

    events = list(reversed(await store.list_provider_events(lane="L1")))
    assert [event.event_type for event in events] == ["turn/started", "turn/completed"]
    assert [event.provider_event_id is not None for event in events] == [True, True]
    turns = await store.list_thread_turns(lane="L1")
    assert len(turns) == 1
    assert turns[0].turn_id == "turn-1"
    assert turns[0].status == "completed"


async def test_reactor_appends_events_without_stable_provider_ids(store: Registry) -> None:
    ctx = make_ctx(store)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="idle")

    await reactor.handle(TokenUsageUpdated("L1"))
    await reactor.handle(TokenUsageUpdated("L1"))

    events = await store.list_provider_events(lane="L1")
    assert [event.event_type for event in events] == [
        "thread/token-usage/updated",
        "thread/token-usage/updated",
    ]


async def test_reactor_indexes_archive_lifecycle_events(store: Registry) -> None:
    ctx = make_ctx(store)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="attached", status="idle")

    await reactor.handle(ThreadArchived("L1"))
    archived = await store.get_lane("L1")
    assert archived.status == "archived"
    runtime = await store.get_lane_runtime_state("L1")
    assert runtime is not None
    assert runtime.status == "archived"

    await reactor.handle(ThreadUnarchived("L1"))
    restored = await store.get_lane("L1")
    assert restored.status == "idle"
    await reactor.handle(ThreadArchived("L1"))
    archived_again = await store.get_lane("L1")
    assert archived_again.status == "archived"
    events = list(reversed(await store.list_provider_events(lane="L1")))
    assert [event.event_type for event in events] == [
        "thread/archived",
        "thread/unarchived",
        "thread/archived",
    ]
    assert [event.provider_event_id for event in events] == [None, None, None]


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
    assert text.startswith("Turn: turn-1\n\ndispatch (sub): ")
    assert "[@target](codex://threads/target)" in text
    assert "↳ completed | done" in text


async def test_reactor_subscription_can_disable_dispatch_attribution(store: Registry) -> None:
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
            attribution=False,
            created_at=_T0,
            updated_at=_T0,
        )
    )

    await reactor.handle(TurnCompleted("target", "turn-1"))

    sent = [kw for name, kw in client.calls if name == "turn_start"]
    assert len(sent) == 1
    text = sent[0]["text"]
    assert isinstance(text, str)
    assert text.startswith("[dispatch] Subscription update for @target")
    assert "dispatch (sub):" not in text


async def test_lane_event_reactor_leaves_response_to_generic_request_manager(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, lambda: _T0))
    await store.add_lane(id="L1", handle="@x", source="own", status="busy")
    await reactor.handle(ApprovalRequested("L1", 7, "command"))
    assert not any(name == "respond_approval" for name, _ in client.calls)
    assert (await store.get_lane("L1")).status == "busy"


async def test_generic_approval_request_fires_waiting_trigger(store: Registry) -> None:
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
    client.server_request_log.append(
        ServerRequestReceived(
            method="item/permissions/requestApproval",
            request_id=7,
            category="approval",
            thread_id="L1",
            raw_params={"permissions": {}},
        )
    )
    await reactor.run()
    assert any(name == "inject_items" for name, _ in client.calls)


async def test_generic_approval_subscription_uses_local_request_id(store: Registry) -> None:
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

    client.server_request_log.append(
        ServerRequestReceived(
            method="execCommandApproval",
            request_id="wire-7",
            category="approval",
            conversation_id="target",
        )
    )
    await reactor.run()

    messages = await store.list_inbox_messages(lane="subscriber")
    assert len(messages) == 1
    assert messages[0].payload["event"] == "approval"
    assert messages[0].payload["request_id"] == 1


async def test_generic_approval_trigger_still_honors_dedupe_guard(
    store: Registry,
) -> None:
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
    client.server_request_log.extend(
        [
            ServerRequestReceived(
                method="item/commandExecution/requestApproval",
                request_id=request_id,
                category="approval",
                thread_id="L1",
            )
            for request_id in (1, 2)
        ]
    )
    await reactor.run()
    injections = [call for name, call in client.calls if name == "inject_items"]
    assert len(injections) == 1
