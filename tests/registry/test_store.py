"""Unit tests for the aiosqlite registry store (in-memory, fixed clock)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import NotFoundError
from outfitter.dispatch.registry.store import Registry


def _clock() -> datetime:
    return datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open(now=_clock)
    try:
        yield s
    finally:
        await s.close()


async def test_add_and_get_lane(store: Registry) -> None:
    lane = await store.add_lane(id="L1", handle="@alpha", source="own", cwd="/work")
    assert lane.id == "L1"
    assert lane.source == "own"
    assert lane.created_at == _clock()
    got = await store.get_lane("L1")
    assert got.handle == "@alpha"


async def test_get_missing_lane_raises_not_found(store: Registry) -> None:
    assert await store.find_lane("nope") is None
    with pytest.raises(NotFoundError):
        await store.get_lane("nope")


async def test_list_excludes_archived_by_default(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@a", source="own")
    await store.add_lane(id="L2", handle="@b", source="attached")
    await store.update_lane_status("L2", "archived")
    active = await store.list_lanes()
    assert [lane.id for lane in active] == ["L1"]
    everything = await store.list_lanes(include_archived=True)
    assert {lane.id for lane in everything} == {"L1", "L2"}


async def test_touch_lane_event_sets_last_event_at(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@a", source="own")
    assert (await store.get_lane("L1")).last_event_at is None
    await store.touch_lane_event("L1")
    assert (await store.get_lane("L1")).last_event_at == _clock()


async def test_log_action_and_recent(store: Registry) -> None:
    await store.log_action("send", lane="L1", detail="hi", outcome="ok")
    await store.log_action("archive", lane="L1", outcome="ok")
    recent = await store.recent_actions(limit=10)
    assert [r.op for r in recent] == ["archive", "send"]  # newest first
    assert recent[1].detail == "hi"


async def test_queued_messages_are_claimed_and_recovered(store: Registry) -> None:
    first = await store.enqueue_message(lane="L1", text="one")
    second = await store.enqueue_message(lane="L1", text="two")

    assert first.id < second.id
    assert await store.pending_message_count("L1") == 2
    pending = await store.next_pending_message("L1")
    assert pending is not None
    assert pending.text == "one"

    assert await store.claim_queued_message(first.id) is True
    assert await store.claim_queued_message(first.id) is False
    assert (await store.get_queued_message(first.id)).status == "sending"
    assert await store.reset_sending_messages() == 1
    assert (await store.get_queued_message(first.id)).status == "pending"

    await store.complete_queued_message(first.id)
    await store.fail_queued_message(second.id, "app_server")
    assert (await store.get_queued_message(first.id)).status == "sent"
    failed = await store.get_queued_message(second.id)
    assert failed.status == "error"
    assert failed.error == "app_server"
