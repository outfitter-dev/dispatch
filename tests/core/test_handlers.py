"""Stateful handler tests (the cases examples can't reach from a fresh ctx)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from outfitter.dispatch.client.models import ThreadInfo, ThreadStatus
from outfitter.dispatch.contracts.errors import AppServerError, AuthorityError, ValidationError
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import (
    AttachInput,
    DiscoverInput,
    LaneInput,
    LaneTextInput,
    LogInput,
    OpenInput,
    RosterInput,
    StatusInput,
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


async def test_status_and_log_reflect_activity(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="one"), ctx)
    await handlers.send(LaneTextInput(lane="lane-1", text="hi"), ctx)
    status = await handlers.status(StatusInput(), ctx)
    assert status.lanes == 1
    assert status.idle == 1
    log = await handlers.show_log(LogInput(limit=10), ctx)
    ops = [a.op for a in log.actions]
    assert "open" in ops
    assert "send" in ops


async def test_attach_is_idempotent(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    first = await handlers.attach_lane(AttachInput(thread="T9"), ctx)
    second = await handlers.attach_lane(AttachInput(thread="T9"), ctx)
    assert first.id == second.id == "T9"
    assert len((await handlers.roster(RosterInput(), ctx)).lanes) == 1


class _HangingResumeClient(FakeLaneClient):
    """A client whose ``thread/resume`` never returns — models a wedged app-server."""

    async def thread_resume(self, thread_id: str) -> ThreadInfo:
        await asyncio.sleep(3600)  # cancelled by the handler's wait_for bound
        raise AssertionError("unreachable")  # pragma: no cover


async def test_attach_resume_timeout_projects_cleanly_and_leaves_registry_empty(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_RESUME_TIMEOUT_S", 0.05)
    ctx = make_ctx(store, _HangingResumeClient())
    with pytest.raises(AppServerError) as excinfo:
        await handlers.attach_lane(AttachInput(thread="STUCK"), ctx)
    assert "timed out" in str(excinfo.value)
    # The bounded failure must not half-register a lane.
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []


async def test_discover_lists_persisted_sessions_from_client(store: Registry) -> None:
    client = FakeLaneClient()
    client.list_result = [
        ThreadInfo(
            id="019e8a09",
            name="Desktop",
            preview="  multi\n  line   preview  ",
            cwd="/work",
            source="cli",
            ephemeral=False,
            status=ThreadStatus(type="idle"),
        ),
        ThreadInfo(id="t2"),  # sparse row: only an id
    ]
    ctx = make_ctx(store, client)
    out = await handlers.discover(DiscoverInput(limit=10), ctx)

    assert [s.id for s in out.sessions] == ["019e8a09", "t2"]
    first = out.sessions[0]
    assert first.name == "Desktop"
    assert first.status == "idle"  # flattened from the status object
    assert first.preview == "multi line preview"  # whitespace collapsed
    assert first.cwd == "/work"
    assert first.source == "cli"
    assert first.ephemeral is False
    # Discovery reads through to the client's thread_list with the requested limit
    # AND state-db only — the latter is what keeps it read-only (no live resume).
    assert any(
        name == "thread_list" and kw["limit"] == 10 and kw["use_state_db_only"] is True
        for name, kw in client.calls
    )
    # ...and registers nothing (pure read; ADR-0005 observe-only untouched).
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []


async def test_discover_shortens_long_preview(store: Registry) -> None:
    client = FakeLaneClient()
    client.list_result = [ThreadInfo(id="t1", preview="x" * 200)]
    ctx = make_ctx(store, client)
    out = await handlers.discover(DiscoverInput(), ctx)
    preview = out.sessions[0].preview
    assert preview is not None
    assert len(preview) <= 80
    assert preview.endswith("…")


async def test_discover_keeps_short_preview_verbatim(store: Registry) -> None:
    exactly_80 = "y" * 80
    client = FakeLaneClient()
    client.list_result = [ThreadInfo(id="t1", preview=exactly_80)]
    ctx = make_ctx(store, client)
    out = await handlers.discover(DiscoverInput(), ctx)
    # At the boundary the preview is returned unchanged — no ellipsis.
    assert out.sessions[0].preview == exactly_80
