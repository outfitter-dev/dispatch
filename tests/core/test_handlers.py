"""Stateful handler tests (the cases examples can't reach from a fresh ctx)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import AuthorityError, ValidationError
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import (
    AttachInput,
    LaneInput,
    LaneTextInput,
    OpenInput,
    RosterInput,
)
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open()
    try:
        yield s
    finally:
        await s.close()


async def test_open_then_send_owned_lane(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    ref = await handlers.open_lane(OpenInput(name="alpha", cwd="/w"), ctx)
    assert ref.id == "lane-1"
    assert ref.handle == "@alpha"
    ack = await handlers.send(LaneTextInput(lane="lane-1", text="ping"), ctx)
    assert ack.accepted is True
    assert any(name == "turn_start" and kw["thread_id"] == "lane-1" for name, kw in client.calls)


async def test_send_resolves_by_handle(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)
    ack = await handlers.send(LaneTextInput(lane="@beta", text="hi"), ctx)
    assert ack.lane == "lane-1"


async def test_send_to_attached_lane_raises_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")
    with pytest.raises(AuthorityError):
        await handlers.send(LaneTextInput(lane="D1", text="nope"), ctx)


async def test_archive_attached_lane_raises_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D2", handle="@desktop", source="attached", status="idle")
    with pytest.raises(AuthorityError):
        await handlers.archive(LaneInput(lane="D2"), ctx)


async def test_steer_attached_lane_raises_authority(store: Registry) -> None:
    # The authority guard precedes the active-turn check: attached lanes never write.
    ctx = make_ctx(store)
    await store.add_lane(id="D3", handle="@desktop", source="attached", status="idle")
    await store.set_active_turn("D3", "turn-1")
    with pytest.raises(AuthorityError):
        await handlers.steer(LaneTextInput(lane="D3", text="nope"), ctx)


async def test_brief_attached_lane_raises_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D4", handle="@desktop", source="attached", status="idle")
    with pytest.raises(AuthorityError):
        await handlers.brief(LaneTextInput(lane="D4", text="nope"), ctx)


async def test_interrupt_attached_lane_raises_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D5", handle="@desktop", source="attached", status="idle")
    with pytest.raises(AuthorityError):
        await handlers.interrupt(LaneInput(lane="D5"), ctx)


async def test_steer_requires_active_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="g"), ctx)
    with pytest.raises(ValidationError):
        await handlers.steer(LaneTextInput(lane="lane-1", text="also mention X"), ctx)
    await store.set_active_turn("lane-1", "turn-7")
    ack = await handlers.steer(LaneTextInput(lane="lane-1", text="also mention X"), ctx)
    assert ack.op == "steer"
    assert any(
        name == "turn_steer" and kw["expected_turn_id"] == "turn-7" for name, kw in client.calls
    )


async def test_roster_then_archive_flips_status(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="one"), ctx)
    roster = await handlers.roster(RosterInput(), ctx)
    assert [lane.handle for lane in roster.lanes] == ["@one"]
    archived = await handlers.archive(LaneInput(lane="lane-1"), ctx)
    assert archived.status == "archived"
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []
    everything = await handlers.roster(RosterInput(include_archived=True), ctx)
    assert len(everything.lanes) == 1


async def test_attach_is_idempotent(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    first = await handlers.attach_lane(AttachInput(thread="T9"), ctx)
    second = await handlers.attach_lane(AttachInput(thread="T9"), ctx)
    assert first.id == second.id == "T9"
    assert len((await handlers.roster(RosterInput(), ctx)).lanes) == 1
