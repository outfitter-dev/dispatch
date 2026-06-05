"""Unit tests for the aiosqlite registry store (in-memory, fixed clock)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import NotFoundError
from outfitter.dispatch.registry.models import LaneSync
from outfitter.dispatch.registry.refs import BASE58BTC_ALPHABET, codex_ref_payload
from outfitter.dispatch.registry.store import SCHEMA_VERSION, Registry


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
    assert lane.ref.startswith("0")
    assert lane.ref_payload == codex_ref_payload("L1")
    assert lane.ref_mixer == BASE58BTC_ALPHABET[0]
    assert lane.source == "own"
    assert lane.created_at == _clock()
    got = await store.get_lane("L1")
    assert got.handle == "@alpha"
    assert await store.find_lane_by_ref(lane.ref) == got


async def test_refs_allocated_for_owned_attached_and_forked_lanes(store: Registry) -> None:
    owned = await store.add_lane(id="owned", handle="@owned", source="own")
    attached = await store.add_lane(id="attached", handle="@attached", source="attached")
    forked = await store.add_lane(id="owned-fork", handle="@forked", source="own")

    assert len({owned.ref, attached.ref, forked.ref}) == 3
    assert owned.ref_source == "0"
    assert attached.ref_source == "0"
    assert forked.ref_source == "0"


async def test_ref_collision_allocates_next_mixer(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("outfitter.dispatch.registry.store.codex_ref_payload", lambda _id: "zzzz")

    first = await store.add_lane(id="first", handle="@first", source="own")
    second = await store.add_lane(id="second", handle="@second", source="own")

    assert first.ref == "0zzzz1"
    assert second.ref == "0zzzz2"
    assert second.ref_mixer == BASE58BTC_ALPHABET[1]


async def test_v2_registry_migration_backfills_unique_refs(tmp_path: Path) -> None:
    db = tmp_path / "registry.sqlite3"
    import aiosqlite

    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lanes (
            id TEXT PRIMARY KEY,
            handle TEXT NOT NULL,
            role TEXT,
            cwd TEXT,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unknown',
            pinned INTEGER NOT NULL DEFAULT 0,
            active_turn_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_event_at TEXT
        );
        INSERT INTO lanes (
            id, handle, source, status, pinned, created_at, updated_at
        ) VALUES
            (
                'B', '@b', 'own', 'idle', 0,
                '2026-06-03T12:00:02+00:00', '2026-06-03T12:00:02+00:00'
            ),
            (
                'A', '@a', 'attached', 'idle', 0,
                '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00'
            );
        PRAGMA user_version = 2;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        lanes = await migrated.list_lanes()
        assert [lane.id for lane in lanes] == ["A", "B"]
        assert len({lane.ref for lane in lanes}) == 2
        assert all(lane.ref for lane in lanes)
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION
    finally:
        await migrated.close()


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


async def test_add_lane_with_sync_commits_lane_sync_and_audit(store: Registry) -> None:
    lane, sync = await store.add_lane_with_sync(
        id="L1",
        handle="@a",
        source="attached",
        cwd="/work",
        status="idle",
        sync=LaneSync(lane="L1", state="metadata", display_name="Desktop"),
        audit_op="attach",
        audit_detail="@a",
    )

    assert lane.id == "L1"
    assert lane.status == "idle"
    assert sync.lane == "L1"
    assert sync.state == "metadata"
    assert sync.display_name == "Desktop"
    assert sync.last_synced_at == _clock().isoformat()
    actions = await store.recent_actions(limit=10)
    assert [(action.op, action.lane, action.detail) for action in actions] == [
        ("attach", "L1", "@a")
    ]


async def test_add_lane_with_sync_rolls_back_if_sync_write_fails(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_sync_write(sync: LaneSync, last_synced_at: str) -> None:
        raise RuntimeError(f"boom: {sync.lane} {last_synced_at}")

    monkeypatch.setattr(store, "_upsert_lane_sync_rows", fail_sync_write)

    with pytest.raises(RuntimeError, match="boom"):
        await store.add_lane_with_sync(
            id="L1",
            handle="@a",
            source="attached",
            sync=LaneSync(lane="L1", state="metadata"),
            audit_op="attach",
            audit_detail="@a",
        )

    assert await store.find_lane("L1") is None
    assert await store.get_lane_sync("L1") is None
    assert await store.recent_actions(limit=10) == []


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


async def test_lane_sync_roundtrip_and_many_lookup(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@a", source="attached", cwd="/work")
    await store.add_lane(id="L2", handle="@b", source="own")

    saved = await store.upsert_lane_sync(
        LaneSync(
            lane="L1",
            state="partial",
            source_path="/tmp/rollout.jsonl",
            source_device=1,
            source_inode=2,
            source_size=3,
            source_mtime_ns=4,
            line_count=5,
            first_offset=0,
            tail_offset=128,
            display_name="Desktop",
            preview="hello",
            cwd="/work",
            source="vscode",
            thread_source="user",
            model_provider="openai",
            model="gpt-5-codex",
            reasoning_effort="low",
            session_id="L1",
            latest_event_at="2026-06-05T10:00:00.000Z",
            latest_turn_id="turn-1",
        )
    )

    assert saved.last_synced_at == _clock().isoformat()
    assert saved.display_name == "Desktop"
    assert saved.source_size == 3

    updated = await store.upsert_lane_sync(
        saved.model_copy(
            update={"state": "complete", "source_size": 10, "latest_turn_id": "turn-2"}
        )
    )
    assert updated.state == "complete"
    assert updated.source_size == 10
    assert updated.latest_turn_id == "turn-2"

    many = await store.get_lane_sync_many(["L1", "L2"])
    assert set(many) == {"L1"}
    assert many["L1"].preview == "hello"
