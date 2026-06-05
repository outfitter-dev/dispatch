"""Stateful handler tests (the cases examples can't reach from a fresh ctx)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from outfitter.dispatch.client.errors import AppServerError as ClientAppServerError
from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.client.models import (
    ThreadGoal,
    ThreadInfo,
    ThreadSearchMatch,
    ThreadSearchResult,
    ThreadStatus,
)
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    AuthorityError,
    NotFoundError,
    ValidationError,
)
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
    LaneRenameInput,
    LaneSyncInput,
    LaneTextInput,
    LogInput,
    NewInput,
    OpenInput,
    RollbackInput,
    RosterInput,
    SearchInput,
    SendInput,
    ShowInput,
    StatusInput,
    ThreadTargetInput,
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
    ref = await handlers.open_lane(OpenInput(name="beta"), ctx)
    ack = await handlers.send(LaneTextInput(lane="@beta", text="hi"), ctx)
    assert ack.lane == "lane-1"
    by_ref = await handlers.send(LaneTextInput(lane=ref.ref, text="again"), ctx)
    assert by_ref.lane == "lane-1"


async def test_send_modes_context_and_interject(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)
    await store.set_active_turn("lane-1", "turn-1")

    context = await handlers.send_message(SendInput(lane="@beta", text="note", mode="context"), ctx)
    interject = await handlers.send_message(
        SendInput(lane="@beta", text="replace", mode="interject"), ctx
    )

    assert context.op == "brief"
    assert interject.op == "interject"
    assert any(name == "inject_items" for name, _ in client.calls)
    assert any(
        name == "turn_interrupt" and kw["thread_id"] == "lane-1" and kw["turn_id"] == "turn-1"
        for name, kw in client.calls
    )
    assert any(name == "turn_start" and kw["text"] == "replace" for name, kw in client.calls)
    assert (await store.get_lane("lane-1")).status == "busy"


async def test_send_queue_persists_when_lane_is_busy(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)
    await store.update_lane_status("lane-1", "busy")

    ack = await handlers.send_message(SendInput(lane="@beta", text="later", mode="queue"), ctx)

    assert ack.op == "queue"
    assert "pending=1" in (ack.detail or "")
    queued = await store.next_pending_message("lane-1")
    assert queued is not None
    assert queued.text == "later"
    assert not any(name == "turn_start" and kw["text"] == "later" for name, kw in client.calls)


async def test_send_queue_starts_immediately_when_lane_is_idle(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)

    ack = await handlers.send_message(SendInput(lane="@beta", text="now", mode="queue"), ctx)

    assert ack.op == "queue"
    assert "pending=0" in (ack.detail or "")
    assert await store.next_pending_message("lane-1") is None
    assert (await store.get_lane("lane-1")).status == "busy"
    sent = await store.get_queued_message(1)
    assert sent.status == "sent"
    assert any(name == "turn_start" and kw["text"] == "now" for name, kw in client.calls)


async def test_lane_rename_updates_registry_and_owned_thread_name(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="old"), ctx)

    out = await handlers.rename_lane(LaneRenameInput(old="@old", new="new"), ctx)

    assert out.handle == "@new"
    assert await store.find_lane_by_handle("@old") is None
    assert (await store.get_lane("lane-1")).handle == "@new"
    assert any(
        name == "thread_set_name" and kw["thread_id"] == "lane-1" and kw["display_name"] == "new"
        for name, kw in client.calls
    )


async def test_lane_rename_updates_attached_thread_name(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")

    out = await handlers.rename_lane(LaneRenameInput(old="@desktop", new="renamed"), ctx)

    assert out.handle == "@renamed"
    assert out.source == "attached"
    assert any(
        name == "thread_set_name" and kw["thread_id"] == "D1" and kw["display_name"] == "renamed"
        for name, kw in client.calls
    )


async def test_lane_rename_can_target_unmanaged_thread(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.rename_lane(LaneRenameInput(old="raw-thread", new="Raw Name"), ctx)

    assert out.id == "raw-thread"
    assert out.managed is False
    assert out.source == "unmanaged"
    assert (await handlers.roster(RosterInput(include_archived=True), ctx)).lanes == []
    assert any(
        name == "thread_set_name"
        and kw["thread_id"] == "raw-thread"
        and kw["display_name"] == "Raw Name"
        for name, kw in client.calls
    )


async def test_unresolved_handle_does_not_fall_through_as_raw_thread_id(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(NotFoundError):
        await handlers.rename_lane(LaneRenameInput(old="@missing", new="new"), ctx)

    assert not client.calls


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


async def test_goal_set_requires_objective_for_new_goal(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    with pytest.raises(ValidationError, match="requires objective"):
        await handlers.goal_set(GoalSetInput(lane="lane-1", status="complete"), ctx)

    assert any(name == "thread_goal_get" for name, _ in client.calls)
    assert not any(name == "thread_goal_set" for name, _ in client.calls)


async def test_goal_set_updates_existing_goal_without_objective(store: Registry) -> None:
    client = FakeLaneClient()
    client.goal_result = ThreadGoal(
        thread_id="lane-1",
        objective="ship",
        status="active",
        tokens_used=0,
        time_used_seconds=0,
        created_at=1,
        updated_at=2,
    )
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.goal_set(GoalSetInput(lane="lane-1", status="complete"), ctx)

    assert out.goal is not None
    assert out.goal.objective == "ship"
    assert out.goal.status == "complete"
    assert any(name == "thread_goal_get" for name, _ in client.calls)
    assert any(
        name == "thread_goal_set" and kw["status"] == "complete" and kw["objective"] is None
        for name, kw in client.calls
    )


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


async def test_archive_attached_lane_updates_thread_and_registry(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await store.add_lane(id="D2", handle="@desktop", source="attached", status="idle")

    out = await handlers.archive(ThreadTargetInput(target="D2"), ctx)

    assert out.status == "archived"
    assert (await store.get_lane("D2")).status == "archived"
    assert any(name == "thread_archive" and kw["thread_id"] == "D2" for name, kw in client.calls)


async def test_steer_attached_lane_raises_authority(store: Registry) -> None:
    # The authority guard precedes the active-turn check: attached lanes do not accept
    # turn-writing operations.
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


async def test_stop_attached_lane_raises_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D5", handle="@desktop", source="attached", status="idle")
    with pytest.raises(AuthorityError):
        await handlers.stop(LaneInput(lane="D5"), ctx)


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


async def test_stop_requires_active_turn(store: Registry) -> None:
    # App Server turn/interrupt requires a turnId; an idle lane has none.
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="g"), ctx)
    with pytest.raises(ValidationError):
        await handlers.stop(LaneInput(lane="lane-1"), ctx)
    assert not any(name == "turn_interrupt" for name, _ in client.calls)
    await store.set_active_turn("lane-1", "turn-9")
    ack = await handlers.stop(LaneInput(lane="lane-1"), ctx)
    assert ack.op == "stop"
    assert any(name == "turn_interrupt" and kw["turn_id"] == "turn-9" for name, kw in client.calls)


async def test_interrupt_requires_active_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="g"), ctx)
    with pytest.raises(ValidationError):
        await handlers.interrupt(LaneInput(lane="lane-1"), ctx)
    assert not any(name == "turn_interrupt" for name, _ in client.calls)


async def test_interject_requires_active_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="g"), ctx)
    with pytest.raises(ValidationError):
        await handlers.send_message(SendInput(lane="lane-1", text="replace", mode="interject"), ctx)
    assert not any(name == "turn_interrupt" for name, _ in client.calls)
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_roster_then_archive_flips_status(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="one"), ctx)
    roster = await handlers.roster(RosterInput(), ctx)
    assert [lane.handle for lane in roster.lanes] == ["@one"]
    archived = await handlers.archive(ThreadTargetInput(target="lane-1"), ctx)
    assert archived.status == "archived"
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []
    everything = await handlers.roster(RosterInput(include_archived=True), ctx)
    assert len(everything.lanes) == 1


class _NoRolloutArchiveClient(FakeLaneClient):
    async def thread_archive(self, thread_id: str) -> None:
        self._record("thread_archive", thread_id=thread_id)
        raise ClientAppServerError(-32600, f"no rollout found for thread id {thread_id}")


async def test_archive_no_rollout_lane_marks_local_lane_archived(store: Registry) -> None:
    client = _NoRolloutArchiveClient()
    ctx = make_ctx(store, client)
    await handlers.new_lane(NewInput(name="smoke", ephemeral=True, send=False), ctx)

    archived = await handlers.archive(ThreadTargetInput(target="lane-1"), ctx)

    assert archived.status == "archived"
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []
    everything = await handlers.roster(RosterInput(include_archived=True), ctx)
    assert [lane.id for lane in everything.lanes] == ["lane-1"]
    assert any(name == "thread_archive" for name, _ in client.calls)


async def test_archive_unmanaged_thread_does_not_register_lane(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.archive(ThreadTargetInput(target="raw-thread"), ctx)

    assert out.id == "raw-thread"
    assert out.managed is False
    assert out.status == "archived"
    assert (await handlers.roster(RosterInput(include_archived=True), ctx)).lanes == []
    assert any(
        name == "thread_archive" and kw["thread_id"] == "raw-thread" for name, kw in client.calls
    )


async def test_restore_managed_lane_unarchives_without_starting_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="one"), ctx)
    client.threads["lane-1"] = ThreadInfo(id="lane-1", status=ThreadStatus(type="idle"))
    await store.update_lane_status("lane-1", "archived")

    restored = await handlers.restore(ThreadTargetInput(target="@one"), ctx)

    assert restored.status == "idle"
    assert (await store.get_lane("lane-1")).status == "idle"
    assert any(
        name == "thread_unarchive" and kw["thread_id"] == "lane-1" for name, kw in client.calls
    )
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_restore_unmanaged_thread_does_not_register_or_start_turn(store: Registry) -> None:
    client = FakeLaneClient()
    client.threads["raw-thread"] = ThreadInfo(id="raw-thread", status=ThreadStatus(type="idle"))
    ctx = make_ctx(store, client)

    restored = await handlers.restore(ThreadTargetInput(target="raw-thread"), ctx)

    assert restored.id == "raw-thread"
    assert restored.managed is False
    assert restored.status == "idle"
    assert (await handlers.roster(RosterInput(include_archived=True), ctx)).lanes == []
    assert any(
        name == "thread_unarchive" and kw["thread_id"] == "raw-thread" for name, kw in client.calls
    )
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_status_and_log_reflect_activity(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="one"), ctx)
    await handlers.send(LaneTextInput(lane="lane-1", text="hi"), ctx)
    status = await handlers.status(StatusInput(), ctx)
    assert status.lanes == 1
    assert status.busy == 1
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
    assert any(name == "thread_read" for name, _ in client.calls)
    assert not any(name == "thread_resume" for name, _ in client.calls)
    sync = await store.get_lane_sync("T9")
    assert sync is not None
    assert sync.state == "metadata"
    actions = await store.recent_actions(limit=10)
    assert [action.op for action in actions] == ["attach"]


class _HangingReadClient(FakeLaneClient):
    """A client whose metadata read never returns — models a wedged app-server."""

    async def thread_read(self, thread_id: str, include_turns: bool = False) -> dict[str, object]:
        await asyncio.sleep(3600)  # cancelled by the handler's wait_for bound
        raise AssertionError("unreachable")  # pragma: no cover


async def test_attach_metadata_timeout_projects_cleanly_and_leaves_registry_empty(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_ATTACH_METADATA_TIMEOUT_S", 0.05)
    ctx = make_ctx(store, _HangingReadClient())
    with pytest.raises(AppServerError) as excinfo:
        await handlers.attach_lane(AttachInput(thread="STUCK"), ctx)
    assert "timed out" in str(excinfo.value)
    # The bounded failure must not half-register a lane.
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []


async def test_attach_invalid_metadata_projects_cleanly_and_leaves_registry_empty(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.read_result = {"data": []}
    ctx = make_ctx(store, client)

    with pytest.raises(AppServerError) as excinfo:
        await handlers.attach_lane(AttachInput(thread="BAD"), ctx)

    assert "invalid payload" in str(excinfo.value)
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []


async def test_attach_with_sync_indexes_jsonl_and_roster_reports_state(
    store: Registry, tmp_path: Path
) -> None:
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"session_meta","timestamp":"2026-06-05T10:00:00.000Z",'
                '"payload":{"id":"T9","cwd":"/work","source":"vscode",'
                '"thread_source":"user","model_provider":"openai"}}',
                '{"type":"turn_context","timestamp":"2026-06-05T10:00:01.000Z",'
                '"payload":{"model":"gpt-5-codex","effort":"low"}}',
                '{"type":"event_msg","timestamp":"2026-06-05T10:00:02.000Z",'
                '"payload":{"type":"task_complete","turn_id":"turn-1"}}',
            ]
        )
        + "\n"
    )
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "T9",
            "name": "Desktop",
            "preview": "hello from desktop",
            "cwd": "/work",
            "source": "vscode",
            "path": str(path),
            "sessionId": "T9",
            "modelProvider": "openai",
        }
    }
    ctx = make_ctx(store, client)

    attached = await handlers.attach_lane(AttachInput(thread="T9", sync=True), ctx)
    detail = await handlers.show(ShowInput(lane="T9"), ctx)
    roster = await handlers.roster(RosterInput(), ctx)

    assert attached.handle == "Desktop"
    assert detail.sync.state == "partial"
    assert detail.sync.latest_turn_id == "turn-1"
    assert detail.sync.source_size == path.stat().st_size
    assert roster.lanes[0].sync.state == "partial"
    assert roster.lanes[0].sync.latest_event_at == "2026-06-05T10:00:02.000Z"
    assert sum(1 for name, _ in client.calls if name == "thread_read") == 1
    assert not any(name == "thread_resume" for name, _ in client.calls)


async def test_lane_sync_can_full_scan_existing_lane(store: Registry, tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        '{"type":"session_meta","timestamp":"2026-06-05T10:00:00.000Z","payload":{"id":"T9"}}\n'
    )
    client = FakeLaneClient()
    client.read_result = {"thread": {"id": "T9", "path": str(path)}}
    ctx = make_ctx(store, client)
    await store.add_lane(id="T9", handle="@desktop", source="attached")

    out = await handlers.sync_lane(LaneSyncInput(lane="@desktop", full=True), ctx)

    assert out.lane == "T9"
    assert out.sync.state == "complete"
    assert out.sync.transcript_partial is False
    assert any(name == "thread_read" for name, _ in client.calls)


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
    # ...and registers nothing (pure read; lane authority untouched).
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


async def test_search_uses_app_server_and_filters_managed_state_and_repo(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    client = FakeLaneClient()
    client.search_result = ThreadSearchResult(
        data=[
            ThreadSearchMatch(
                snippet="needle in managed",
                thread=ThreadInfo(
                    id="M1",
                    name="Managed",
                    cwd=str(repo / "subdir"),
                    created_at=100_000,
                    updated_at=200_000,
                    preview="managed preview",
                    status=ThreadStatus(type="idle"),
                ),
            ),
            ThreadSearchMatch(
                snippet="needle in unmanaged",
                thread=ThreadInfo(
                    id="U1",
                    name="Unmanaged",
                    cwd=str(outside),
                    created_at=100_000,
                    updated_at=200_000,
                    status=ThreadStatus(type="idle"),
                ),
            ),
        ]
    )
    ctx = make_ctx(store, client)
    await store.add_lane(id="M1", handle="@managed", source="attached", status="idle")

    out = await handlers.search(
        SearchInput(query="needle", managed=True, repo=str(repo), since="1970-01-01"),
        ctx,
    )

    assert [match.id for match in out.matches] == ["M1"]
    assert out.matches[0].handle == "@managed"
    assert out.matches[0].managed is True
    assert out.scanned == 2
    assert any(
        name == "thread_search" and kw["search_term"] == "needle" and kw["sort_key"] == "updated_at"
        for name, kw in client.calls
    )


async def test_search_can_filter_unmanaged_threads(store: Registry) -> None:
    client = FakeLaneClient()
    client.search_result = ThreadSearchResult(
        data=[
            ThreadSearchMatch(snippet="needle", thread=ThreadInfo(id="managed")),
            ThreadSearchMatch(snippet="needle", thread=ThreadInfo(id="raw")),
        ]
    )
    ctx = make_ctx(store, client)
    await store.add_lane(id="managed", handle="@managed", source="attached", status="idle")

    out = await handlers.search(SearchInput(query="needle", unmanaged=True), ctx)

    assert [match.id for match in out.matches] == ["raw"]
    assert out.matches[0].source == "unmanaged"


async def test_lane_search_reads_one_thread_transcript(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "name": "Docs",
            "cwd": "/work",
            "updatedAt": 200,
            "turns": [
                {
                    "id": "t1",
                    "items": [
                        {"id": "a1", "type": "agentMessage", "text": "nothing here"},
                        {"id": "a2", "type": "agentMessage", "text": "needle appears"},
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="docs"), ctx)

    out = await handlers.search(SearchInput(query="needle", lane="@docs"), ctx)

    assert [match.snippet for match in out.matches] == ["needle appears"]
    assert out.matches[0].handle == "@docs"
    assert out.scanned == 2
    assert any(
        name == "thread_read" and kw["thread_id"] == "lane-1" and kw["include_turns"] is True
        for name, kw in client.calls
    )
    assert not any(name == "thread_search" for name, _ in client.calls)


async def test_search_rejects_conflicting_managed_filters(store: Registry) -> None:
    ctx = make_ctx(store)
    with pytest.raises(ValidationError):
        await handlers.search(SearchInput(query="needle", managed=True, unmanaged=True), ctx)
