"""Trigger persistence: when/action/guard round-trip through SQLite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import NotFoundError
from outfitter.dispatch.registry.models import (
    CronWhen,
    EventWhen,
    Guard,
    IntervalWhen,
    SendAction,
    Trigger,
)
from outfitter.dispatch.registry.store import Registry


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open()
    try:
        yield s
    finally:
        await s.close()


async def test_trigger_roundtrip_preserves_union_variants(store: Registry) -> None:
    trigger = Trigger(
        id="t1",
        name="ping",
        lane="@x",
        when=IntervalWhen(seconds=60),
        action=SendAction(text="status?"),
        guard=Guard(idle_only=True, min_interval=30),
    )
    await store.add_trigger(trigger)
    got = await store.get_trigger("t1")
    assert isinstance(got.when, IntervalWhen)
    assert got.when.seconds == 60
    assert isinstance(got.action, SendAction)
    assert got.action.text == "status?"
    assert got.guard.idle_only is True
    assert got.guard.min_interval == 30


async def test_list_enable_and_remove(store: Registry) -> None:
    await store.add_trigger(
        Trigger(
            id="t1",
            name="a",
            lane="@x",
            when=EventWhen(event="turn_completed"),
            action=SendAction(text="hi"),
        )
    )
    await store.add_trigger(
        Trigger(
            id="t2",
            name="b",
            lane="@y",
            when=CronWhen(expr="* * * * *"),
            action=SendAction(text="yo"),
        )
    )
    assert [t.id for t in await store.list_triggers()] == ["t1", "t2"]

    await store.set_trigger_enabled("t1", False)
    assert (await store.get_trigger("t1")).enabled is False

    assert await store.remove_trigger("t1") is True
    assert [t.id for t in await store.list_triggers()] == ["t2"]
    assert await store.remove_trigger("missing") is False
    with pytest.raises(NotFoundError):
        await store.get_trigger("t1")
