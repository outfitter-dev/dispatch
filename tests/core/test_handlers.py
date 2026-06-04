"""Stateful handler tests (the cases examples can't reach from a fresh ctx)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.client.models import ThreadGoal, ThreadInfo, ThreadStatus
from outfitter.dispatch.contracts.errors import AppServerError, AuthorityError, ValidationError
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import (
    AttachInput,
    CompactInput,
    DiscoverInput,
    ForkInput,
    GoalClearInput,
    GoalGetInput,
    GoalSetInput,
    LaneInput,
    LaneTextInput,
    LogInput,
    NewInput,
    OpenInput,
    RollbackInput,
    RosterInput,
    ShowInput,
    StatusInput,
    TranscriptInput,
    WatchInput,
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


async def test_new_lane_sets_name_and_sends_initial_turn(store: Registry, tmp_path: Path) -> None:
    repo = tmp_path / "dispatch"
    repo.mkdir()
    (repo / ".git").mkdir()
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="builder",
            cwd=str(repo),
            text="start",
            sandbox="workspace-write",
            approval_policy="on-request",
            effort="low",
            model="gpt-5-codex",
            developer_instructions="stay focused",
        ),
        ctx,
    )

    assert out.handle == "@[dispatch] builder"
    assert out.sent is True
    assert any(
        name == "thread_start"
        and kw["sandbox"] == "workspace-write"
        and kw["approval_policy"] == "on-request"
        and kw["model"] == "gpt-5-codex"
        and kw["developer_instructions"] == "stay focused"
        for name, kw in client.calls
    )
    assert any(
        name == "thread_set_name" and kw["display_name"] == "[dispatch] builder"
        for name, kw in client.calls
    )
    assert any(
        name == "turn_start"
        and kw["text"] == "start"
        and kw["sandbox_policy"] == {"type": "workspaceWrite"}
        and kw["effort"] == "low"
        for name, kw in client.calls
    )


async def test_new_lane_no_send_registers_without_turn(store: Registry, tmp_path: Path) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(name="idle", cwd=str(tmp_path), text="do not send", send=False), ctx
    )

    assert out.sent is False
    assert (await store.find_lane("lane-1")) is not None
    assert not any(name == "turn_start" for name, _ in client.calls)


class _FailingTurnClient(FakeLaneClient):
    async def turn_start(self, *args: object, **kwargs: object) -> dict[str, object]:
        self._record("turn_start", failed=True)
        raise TransportError("boom")


async def test_new_lane_initial_send_failure_leaves_lane_registered(
    store: Registry, tmp_path: Path
) -> None:
    client = _FailingTurnClient()
    ctx = make_ctx(store, client)

    with pytest.raises(TransportError):
        await handlers.new_lane(NewInput(name="still-here", cwd=str(tmp_path), text="boom"), ctx)

    lane = await store.find_lane("lane-1")
    assert lane is not None
    log = await handlers.show_log(LogInput(limit=10), ctx)
    send_records = [record for record in log.actions if record.op == "send"]
    assert send_records
    assert send_records[0].outcome == "app_server"


async def test_send_resolves_by_handle(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)
    ack = await handlers.send(LaneTextInput(lane="@beta", text="hi"), ctx)
    assert ack.lane == "lane-1"


async def test_show_can_include_compact_transcript(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "turn-1",
                    "items": [
                        {
                            "id": "u1",
                            "type": "userMessage",
                            "content": [{"type": "text", "text": "hello"}],
                        },
                        {"id": "a1", "type": "agentMessage", "text": "hi"},
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.show(ShowInput(lane="@alpha", include_transcript=True, max_items=2), ctx)

    assert [item.text for item in out.transcript] == ["hello", "hi"]
    assert any(
        name == "thread_read" and kw["thread_id"] == "lane-1" and kw["include_turns"] is True
        for name, kw in client.calls
    )


async def test_transcript_reads_persisted_turn_items(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [{"id": "a1", "type": "agentMessage", "text": "done"}],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.transcript(TranscriptInput(lane="lane-1", limit=1), ctx)

    assert out.lane == "lane-1"
    assert len(out.items) == 1
    assert out.items[0].text == "done"


async def test_watch_collects_bounded_raw_events(store: Registry) -> None:
    client = FakeLaneClient()
    client.raw_log = [
        {"method": "turn/started", "params": {"threadId": "lane-1", "turnId": "t1"}},
        {"id": 7, "method": "item/tool/requestUserInput", "params": {"threadId": "lane-1"}},
    ]
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.watch(WatchInput(lane="lane-1", limit=2, timeout=1), ctx)

    assert out.timed_out is False
    assert [event.method for event in out.events] == ["turn/started", "item/tool/requestUserInput"]
    assert out.events[1].request_id == 7


async def test_watch_zero_timeout_returns_immediately(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.watch(WatchInput(lane="lane-1", timeout=0), ctx)

    assert out.events == []
    assert out.timed_out is True


async def test_goal_get_set_and_clear_use_native_goal_api(store: Registry) -> None:
    client = FakeLaneClient()
    client.goal_result = ThreadGoal(
        thread_id="lane-1",
        objective="ship",
        status="active",
        tokens_used=5,
        time_used_seconds=6,
        created_at=1,
        updated_at=2,
    )
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    got = await handlers.goal_get(GoalGetInput(lane="@alpha"), ctx)
    assert got.goal is not None
    assert got.goal.objective == "ship"

    set_out = await handlers.goal_set(
        GoalSetInput(lane="lane-1", objective="finish", token_budget=100),
        ctx,
    )
    assert set_out.goal is not None
    assert set_out.goal.objective == "finish"
    assert any(
        name == "thread_goal_set" and kw["objective"] == "finish" and kw["token_budget"] == 100
        for name, kw in client.calls
    )

    cleared = await handlers.goal_clear(GoalClearInput(lane="lane-1"), ctx)
    assert cleared.goal is None
    assert any(
        name == "thread_goal_clear" and kw["thread_id"] == "lane-1" for name, kw in client.calls
    )


async def test_goal_set_requires_a_change(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    with pytest.raises(ValidationError):
        await handlers.goal_set(GoalSetInput(lane="lane-1"), ctx)


async def test_fork_registers_new_owned_lane(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha", cwd="/source"), ctx)

    out = await handlers.fork(
        ForkInput(
            lane="@alpha",
            name="alpha-copy",
            cwd="/fork",
            sandbox="workspace-write",
            approval_policy="on-request",
            ephemeral=True,
        ),
        ctx,
    )

    assert out.id == "lane-1-fork"
    assert out.handle == "@alpha-copy"
    lane = await store.find_lane("lane-1-fork")
    assert lane is not None
    assert lane.source == "own"
    assert lane.cwd == "/fork"
    assert any(
        name == "thread_fork"
        and kw["thread_id"] == "lane-1"
        and kw["sandbox"] == "workspace-write"
        and kw["approval_policy"] == "on-request"
        for name, kw in client.calls
    )


async def test_rollback_and_compact_owned_lane(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)
    await store.set_active_turn("lane-1", "turn-9")
    await store.update_lane_status("lane-1", "busy")

    rolled = await handlers.rollback(RollbackInput(lane="lane-1", turns=2), ctx)
    assert rolled.status == "idle"
    assert any(
        name == "thread_rollback" and kw["thread_id"] == "lane-1" and kw["num_turns"] == 2
        for name, kw in client.calls
    )

    compacted = await handlers.compact(CompactInput(lane="@alpha"), ctx)
    assert compacted.op == "compact"
    assert any(
        name == "thread_compact_start" and kw["thread_id"] == "lane-1" for name, kw in client.calls
    )


async def test_history_control_ops_on_attached_lane_raise_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D6", handle="@desktop", source="attached", status="idle")

    with pytest.raises(AuthorityError):
        await handlers.goal_clear(GoalClearInput(lane="D6"), ctx)
    with pytest.raises(AuthorityError):
        await handlers.fork(ForkInput(lane="D6", name="copy"), ctx)
    with pytest.raises(AuthorityError):
        await handlers.rollback(RollbackInput(lane="D6"), ctx)
    with pytest.raises(AuthorityError):
        await handlers.compact(CompactInput(lane="D6"), ctx)


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
