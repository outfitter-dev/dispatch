"""Unit tests for the aiosqlite registry store (in-memory, fixed clock)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import NotFoundError
from outfitter.dispatch.registry.models import LaneRuntimeSettings, LaneSync, Subscription
from outfitter.dispatch.registry.refs import BASE58BTC_ALPHABET, codex_ref_payload
from outfitter.dispatch.registry.store import SCHEMA_VERSION, Registry
from tests.fixtures.registry.builders import (
    lane_model_settings,
    lane_runtime_settings,
    lane_runtime_state,
    message_receipt,
    model_catalog_entry,
    provider_event,
    thread_item,
    thread_item_ref,
    thread_turn,
)


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


async def test_registry_enforces_foreign_keys(store: Registry) -> None:
    async with store._conn.execute("PRAGMA foreign_keys") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 1

    with pytest.raises(aiosqlite.IntegrityError):
        await store.upsert_lane_sync(LaneSync(lane="missing", state="metadata"))

    with pytest.raises(aiosqlite.IntegrityError):
        await store.enqueue_message(lane="missing", text="lost")


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
        assert all(lane.latest_turn_status is None for lane in lanes)
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION
    finally:
        await migrated.close()


async def test_migrates_v3_registry_with_runtime_columns(tmp_path: Path) -> None:
    db = tmp_path / "registry-v3.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lanes (
            id TEXT PRIMARY KEY,
            ref TEXT NOT NULL UNIQUE,
            ref_source TEXT NOT NULL,
            ref_payload TEXT NOT NULL,
            ref_mixer TEXT NOT NULL,
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
            id, ref, ref_source, ref_payload, ref_mixer, handle, source, status,
            pinned, created_at, updated_at
        ) VALUES (
            'A', '0abc1', '0', 'abc', '1', '@a', 'own', 'idle', 0,
            '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00'
        );
        CREATE TABLE triggers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            lane_selector TEXT NOT NULL,
            when_spec TEXT NOT NULL,
            action_spec TEXT NOT NULL,
            guard_spec TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            last_fired_at TEXT
        );
        CREATE TABLE actions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            op TEXT NOT NULL,
            lane TEXT,
            trigger_id TEXT,
            detail TEXT,
            outcome TEXT NOT NULL DEFAULT 'ok'
        );
        CREATE TABLE queued_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT
        );
        CREATE TABLE lane_sync_sources (
            lane TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            source_path TEXT,
            source_device INTEGER,
            source_inode INTEGER,
            source_size INTEGER,
            source_mtime_ns INTEGER,
            line_count INTEGER,
            first_offset INTEGER,
            tail_offset INTEGER,
            last_synced_at TEXT,
            error TEXT
        );
        CREATE TABLE lane_snapshots (
            lane TEXT PRIMARY KEY,
            display_name TEXT,
            preview TEXT,
            cwd TEXT,
            source TEXT,
            thread_source TEXT,
            model_provider TEXT,
            model TEXT,
            reasoning_effort TEXT,
            session_id TEXT,
            latest_event_at TEXT,
            latest_turn_id TEXT,
            transcript_partial INTEGER NOT NULL DEFAULT 1
        );
        PRAGMA user_version = 3;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        lane = await migrated.get_lane("A")
        assert lane.latest_turn_id is None
        assert lane.latest_turn_status is None
        catalog = await migrated.list_model_catalog()
        assert catalog == []
        assert await migrated.get_lane_model_settings("A") is None
        await migrated.record_turn_failed("A", "turn-1", "unsupported model")
        failed = await migrated.get_lane("A")
        assert failed.status == "error"
        assert failed.latest_turn_id == "turn-1"
        assert failed.latest_turn_status == "failed"
        assert failed.latest_error == "unsupported model"
    finally:
        await migrated.close()


async def test_model_catalog_and_lane_model_settings_roundtrip(store: Registry) -> None:
    now = store.now_iso()
    entry = model_catalog_entry(now=now)
    await store.upsert_model_catalog([entry])
    refreshed = entry.model_copy(update={"last_seen_at": "2026-06-03T12:05:00+00:00"})
    await store.upsert_model_catalog([refreshed])

    got = await store.get_model_catalog_entry("gpt-5.5")
    assert got == refreshed.model_copy(update={"first_seen_at": now})
    assert await store.list_model_catalog() == [got]


async def test_lane_model_settings_roundtrip(store: Registry) -> None:
    now = store.now_iso()
    lane = await store.add_lane(id="L1", handle="@alpha", source="own")
    settings = lane_model_settings(lane=lane.id, updated_at=now)

    await store.upsert_lane_model_settings(settings)

    assert await store.get_lane_model_settings(lane.id) == settings
    assert await store.get_lane_model_settings_many([lane.id, "missing"]) == {lane.id: settings}


async def test_provider_event_history_index_roundtrips_and_dedupes(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@lane", source="own", status="idle")

    first = await store.record_provider_event(provider_event())
    duplicate = await store.record_provider_event(
        provider_event(event_type="turn/started", received_at="2026-06-11T12:00:05+00:00")
    )

    assert first.id == duplicate.id
    assert first.summary == {"status": "started"}
    assert first.payload == {"method": "turn/started", "params": {"turnId": "turn-1"}}
    listed = await store.list_provider_events(lane="L1")
    assert [event.id for event in listed] == [first.id]

    started = await store.upsert_thread_turn(thread_turn())
    completed = await store.upsert_thread_turn(
        started.model_copy(
            update={
                "status": "completed",
                "completed_at": "2026-06-11T12:00:10+00:00",
                "completion_source": "codex-event",
                "updated_at": "2026-06-11T12:00:10+00:00",
            }
        )
    )
    assert completed.status == "completed"
    assert (await store.list_thread_turns(lane="L1")) == [completed]

    item = await store.upsert_thread_item(
        thread_item(position=1),
        refs=[thread_item_ref(), thread_item_ref(ref_type="file", ref_value="README.md")],
    )
    refs = await store.list_thread_item_refs(item)
    assert [ref.ref_type for ref in refs] == ["file", "tool"]
    assert (await store.list_thread_items(lane="L1", turn_id="turn-1")) == [item]
    older = await store.upsert_thread_item(thread_item(item_id="item-0", position=0))
    newer = await store.upsert_thread_item(thread_item(item_id="item-2", position=2))
    all_items = await store.list_thread_items(lane="L1", limit=None)
    assert [found.item_id for found in all_items] == [newer.item_id, item.item_id, older.item_id]
    assert [found.item_id for found in await store.list_thread_items(lane="L1", limit=1)] == [
        newer.item_id
    ]

    created = await store.upsert_message_receipt(message_receipt())
    accepted = await store.upsert_message_receipt(
        created.model_copy(
            update={
                "status": "accepted",
                "accepted_at": "2026-06-11T12:00:03+00:00",
                "turn_id": "turn-1",
                "updated_at": "2026-06-11T12:00:03+00:00",
            }
        )
    )
    assert created.id == accepted.id
    assert accepted.status == "accepted"
    assert (await store.list_message_receipts(lane="L1")) == [accepted]

    runtime = await store.upsert_lane_runtime_state(lane_runtime_state())
    assert runtime.status == "busy"
    assert runtime.latest_turn_status == "started"
    assert await store.get_lane_runtime_state("L1") == runtime


async def test_thread_history_snapshot_batches_rows_prunes_and_summarizes(
    store: Registry,
) -> None:
    await store.add_lane(id="L1", handle="@lane", source="own", status="idle")
    await store.upsert_thread_turn(thread_turn(turn_id="stale-turn"))
    stale = await store.upsert_thread_item(thread_item(item_id="stale-item"))

    turn = thread_turn(status="completed")
    item = thread_item(item_id="item-1", position=1)
    await store.upsert_thread_history_snapshot(
        turns=[turn],
        items=[
            (
                item,
                [
                    thread_item_ref(item_id="item-1", ref_type="tool", ref_value="bash"),
                    thread_item_ref(item_id="item-1", ref_type="file", ref_value="README.md"),
                ],
            )
        ],
        provider="codex",
        provider_thread_id="thread-1",
        turn_ids={"turn-1"},
        item_ids={"item-1"},
    )

    with pytest.raises(NotFoundError):
        await store.get_thread_item(stale.provider, stale.provider_thread_id, stale.item_id)
    assert [found.turn_id for found in await store.list_thread_turns(lane="L1")] == ["turn-1"]
    assert [found.item_id for found in await store.list_thread_items(lane="L1")] == ["item-1"]
    refs_by_item = await store.list_thread_item_refs_many([item])
    assert [(ref.ref_type, ref.ref_value) for ref in refs_by_item["item-1"]] == [
        ("file", "README.md"),
        ("tool", "bash"),
    ]
    stats = await store.get_thread_history_summary_stats(lane="L1")
    assert stats.turns == 1
    assert stats.items == 1
    assert stats.tool_calls == 1
    assert [tool.tool for tool in stats.tools] == ["bash"]
    assert [(file.path, file.count) for file in stats.files] == [("README.md", 1)]


async def test_concurrent_lane_sync_writes_are_serialized(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@lane", source="attached")

    await asyncio.gather(
        *(
            store.upsert_lane_sync(
                LaneSync(lane="L1", state="metadata", display_name=f"Lane {index}")
            )
            for index in range(8)
        )
    )

    sync = await store.get_lane_sync("L1")
    assert sync is not None
    assert sync.display_name is not None


async def test_provider_event_payload_is_stored_compactly(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@lane", source="own", status="idle")
    payload = {"method": "m", "params": {str(index): index for index in range(8)}}

    saved = await store.record_provider_event(
        provider_event().model_copy(update={"payload": payload})
    )

    async with store._conn.execute(
        "SELECT payload, length(payload) AS bytes FROM provider_events WHERE id = ?",
        (saved.id,),
    ) as cur:
        row = await cur.fetchone()
    compact = json.dumps(payload, separators=(",", ":"))
    assert row is not None
    assert row["payload"] == compact
    assert row["bytes"] == len(compact.encode("utf-8"))


async def test_thread_item_payload_is_stored_compactly(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@lane", source="own", status="idle")
    payload = {"type": "toolCall", "metadata": {str(index): index for index in range(8)}}

    saved = await store.upsert_thread_item(
        thread_item().model_copy(update={"payload": payload, "raw_retained": True})
    )

    async with store._conn.execute(
        "SELECT payload, length(payload) AS bytes FROM thread_items WHERE item_id = ?",
        (saved.item_id,),
    ) as cur:
        row = await cur.fetchone()
    compact = json.dumps(payload, separators=(",", ":"))
    assert row is not None
    assert row["payload"] == compact
    assert row["bytes"] == len(compact.encode("utf-8"))


async def test_v10_registry_migration_adds_provider_history_tables(tmp_path: Path) -> None:
    db = tmp_path / "registry-v10.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lanes (
            id TEXT PRIMARY KEY,
            ref TEXT NOT NULL UNIQUE,
            ref_source TEXT NOT NULL,
            ref_payload TEXT NOT NULL,
            ref_mixer TEXT NOT NULL,
            handle TEXT NOT NULL,
            role TEXT,
            cwd TEXT,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unknown',
            pinned INTEGER NOT NULL DEFAULT 0,
            active_turn_id TEXT,
            latest_turn_id TEXT,
            latest_turn_status TEXT,
            latest_error TEXT,
            latest_error_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_event_at TEXT
        );
        INSERT INTO lanes (
            id, ref, ref_source, ref_payload, ref_mixer, handle, source, status,
            pinned, created_at, updated_at
        ) VALUES (
            'L1', '0abc1', '0', 'abc', '1', '@lane', 'own', 'idle', 0,
            '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00'
        );
        PRAGMA user_version = 10;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        saved = await migrated.record_provider_event(provider_event())
        assert saved.lane == "L1"
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION
        async with migrated._conn.execute("PRAGMA table_info(thread_items)") as cur:
            columns = {str(column["name"]) for column in await cur.fetchall()}
        assert "position" in columns
        async with migrated._conn.execute(
            "PRAGMA index_info(idx_thread_items_lane_inserted)"
        ) as cur:
            index_columns = [str(column["name"]) for column in await cur.fetchall()]
        assert index_columns == ["lane", "position", "inserted_at"]
    finally:
        await migrated.close()


async def test_lane_runtime_settings_roundtrip(store: Registry) -> None:
    lane = await store.add_lane(id="L1", handle="@alpha", source="own")
    settings = lane_runtime_settings(lane=lane.id, updated_at=store.now_iso())

    await store.upsert_lane_runtime_settings(settings)

    assert await store.get_lane_runtime_settings(lane.id) == settings
    assert await store.get_lane_runtime_settings("missing") is None


async def test_migration_allows_inherited_runtime_policy(tmp_path: Path) -> None:
    db = tmp_path / "registry.db"
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(
            """
            CREATE TABLE lanes (
                id TEXT PRIMARY KEY,
                ref TEXT NOT NULL UNIQUE,
                ref_source TEXT NOT NULL,
                ref_payload TEXT NOT NULL,
                ref_mixer TEXT NOT NULL,
                handle TEXT NOT NULL,
                role TEXT,
                cwd TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unknown',
                pinned INTEGER NOT NULL DEFAULT 0,
                active_turn_id TEXT,
                latest_turn_id TEXT,
                latest_turn_status TEXT,
                latest_error TEXT,
                latest_error_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_event_at TEXT
            );
            CREATE TABLE lane_runtime_settings (
                lane TEXT PRIMARY KEY,
                sandbox TEXT NOT NULL DEFAULT 'read-only',
                approval_policy TEXT NOT NULL DEFAULT 'never',
                approvals_reviewer TEXT,
                effort TEXT,
                summary TEXT,
                model TEXT,
                service_tier TEXT,
                output_schema TEXT,
                personality TEXT,
                updated_at TEXT NOT NULL
            );
            PRAGMA user_version = 7;
            """
        )
        await conn.execute(
            "INSERT INTO lanes (id, ref, ref_source, ref_payload, ref_mixer, handle, "
            "source, status, created_at, updated_at) "
            "VALUES ('L1', '0BGeK1', '0', 'payload', '00', '@a', 'own', 'idle', ?, ?)",
            (_clock().isoformat(), _clock().isoformat()),
        )
        await conn.commit()

    migrated = await Registry.open(db, now=_clock)
    inherited = LaneRuntimeSettings(
        lane="L1",
        sandbox=None,
        approval_policy=None,
        updated_at=migrated.now_iso(),
    )
    await migrated.upsert_lane_runtime_settings(inherited)

    assert await migrated.get_lane_runtime_settings("L1") == inherited
    await migrated.close()


async def test_get_missing_lane_raises_not_found(store: Registry) -> None:
    assert await store.find_lane("nope") is None
    with pytest.raises(NotFoundError):
        await store.get_lane("nope")


async def test_turn_request_failure_clears_stale_turn_id(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@a", source="own")
    await store.record_turn_started("L1", "turn-1")
    await store.record_turn_failed("L1", "turn-1", "old failure")

    await store.record_turn_request_failed("L1", "request rejected")

    lane = await store.get_lane("L1")
    assert lane.latest_turn_id is None
    assert lane.latest_turn_status == "failed"
    assert lane.latest_error == "request rejected"


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
    await store.add_lane(id="L1", handle="@a", source="own")
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


async def test_inbox_messages_roundtrip_and_ack(store: Registry) -> None:
    await store.add_lane(id="target", handle="@target", source="own")
    await store.add_lane(id="subscriber", handle="@subscriber", source="own")

    message = await store.add_inbox_message(
        recipient_lane="subscriber",
        source_lane="target",
        kind="subscription_update",
        subject="@target done",
        body="finished",
        payload={"event": "completed"},
        delivery="inbox",
    )

    assert message.id == 1
    assert message.state == "pending"
    assert message.payload == {"event": "completed"}
    listed = await store.list_inbox_messages(lane="subscriber")
    assert [m.id for m in listed] == [message.id]

    acked = await store.ack_inbox_message(message.id)
    assert acked.state == "acked"
    assert acked.acked_at == _clock()


async def test_subscriptions_roundtrip_and_once_match(store: Registry) -> None:
    await store.add_lane(id="target", handle="@target", source="own")
    await store.add_lane(id="subscriber", handle="@subscriber", source="own")
    now = _clock()

    subscription = await store.add_subscription(
        Subscription(
            id="sub_1",
            target_lane="target",
            subscriber_lane="subscriber",
            when="done",
            delivery="turn",
            deliver="idle",
            tail=1,
            once=True,
            ack="auto",
            attribution=False,
            created_at=now,
            updated_at=now,
        )
    )
    assert subscription.attribution is False
    message = await store.add_inbox_message(
        recipient_lane="subscriber",
        source_lane="target",
        subscription_id=subscription.id,
        subject="@target done",
        body="finished",
    )

    matched = await store.mark_subscription_matched(subscription.id, inbox_message_id=message.id)

    assert matched.state == "done"
    assert matched.last_inbox_message_id == message.id
    assert matched.last_matched_at == _clock()


async def test_v9_migration_adds_subscription_attribution_column(tmp_path: Path) -> None:
    db = tmp_path / "registry-v9-subscriptions.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lanes (
            id TEXT PRIMARY KEY,
            ref TEXT NOT NULL UNIQUE,
            ref_source TEXT NOT NULL,
            ref_payload TEXT NOT NULL,
            ref_mixer TEXT NOT NULL,
            handle TEXT NOT NULL,
            role TEXT,
            cwd TEXT,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unknown',
            pinned INTEGER NOT NULL DEFAULT 0,
            active_turn_id TEXT,
            latest_turn_id TEXT,
            latest_turn_status TEXT,
            latest_error TEXT,
            latest_error_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_event_at TEXT
        );
        INSERT INTO lanes (
            id, ref, ref_source, ref_payload, ref_mixer, handle, source, status,
            pinned, created_at, updated_at
        ) VALUES
            ('target', '0aaa1', '0', 'aaa', '1', '@target', 'own', 'idle', 0,
             '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00'),
            ('subscriber', '0bbb1', '0', 'bbb', '1', '@subscriber', 'own', 'idle', 0,
             '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00');
        CREATE TABLE inbox_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_lane TEXT NOT NULL,
            source_lane TEXT,
            subscription_id TEXT,
            kind TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'pending',
            delivery TEXT NOT NULL DEFAULT 'inbox',
            queued_message_id INTEGER,
            created_at TEXT NOT NULL,
            delivered_at TEXT,
            acked_at TEXT
        );
        CREATE TABLE subscriptions (
            id TEXT PRIMARY KEY,
            target_lane TEXT NOT NULL,
            subscriber_lane TEXT NOT NULL,
            when_spec TEXT NOT NULL,
            delivery TEXT NOT NULL,
            deliver_policy TEXT NOT NULL,
            tail INTEGER NOT NULL DEFAULT 1,
            once INTEGER NOT NULL DEFAULT 1,
            ack_policy TEXT NOT NULL DEFAULT 'auto',
            state TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_matched_at TEXT,
            last_inbox_message_id INTEGER
        );
        INSERT INTO subscriptions (
            id, target_lane, subscriber_lane, when_spec, delivery, deliver_policy,
            tail, once, ack_policy, state, created_at, updated_at
        ) VALUES (
            'sub_1', 'target', 'subscriber', 'done', 'turn', 'idle',
            1, 1, 'auto', 'active',
            '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00'
        );
        PRAGMA user_version = 9;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        subscription = await migrated.get_subscription("sub_1")
        assert subscription.attribution is True
        async with migrated._conn.execute("PRAGMA table_info(subscriptions)") as cur:
            rows = await cur.fetchall()
        assert "attribution" in {row["name"] for row in rows}
    finally:
        await migrated.close()


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
            model="test-model",
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


async def test_lane_sync_upsert_rolls_back_source_row_if_snapshot_write_fails(
    store: Registry,
) -> None:
    await store.add_lane(id="L1", handle="@a", source="attached")
    await store._conn.execute(
        """
        CREATE TRIGGER fail_lane_snapshot_insert
        BEFORE INSERT ON lane_snapshots
        BEGIN
            SELECT RAISE(FAIL, 'snapshot failed');
        END;
        """
    )
    await store._conn.commit()

    with pytest.raises(aiosqlite.IntegrityError, match="snapshot failed"):
        await store.upsert_lane_sync(LaneSync(lane="L1", state="metadata"))

    assert await store.get_lane_sync("L1") is None


async def test_v5_migration_adds_queue_foreign_key_and_drops_orphans(tmp_path: Path) -> None:
    db = tmp_path / "registry-v5.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lanes (
            id TEXT PRIMARY KEY,
            ref TEXT NOT NULL UNIQUE,
            ref_source TEXT NOT NULL,
            ref_payload TEXT NOT NULL,
            ref_mixer TEXT NOT NULL,
            handle TEXT NOT NULL,
            role TEXT,
            cwd TEXT,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unknown',
            pinned INTEGER NOT NULL DEFAULT 0,
            active_turn_id TEXT,
            latest_turn_id TEXT,
            latest_turn_status TEXT,
            latest_error TEXT,
            latest_error_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_event_at TEXT
        );
        INSERT INTO lanes (
            id, ref, ref_source, ref_payload, ref_mixer, handle, source, status,
            pinned, created_at, updated_at
        ) VALUES (
            'L1', '0abc1', '0', 'abc', '1', '@a', 'own', 'idle', 0,
            '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00'
        );
        CREATE TABLE queued_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT
        );
        INSERT INTO queued_messages (lane, text, status, created_at, updated_at) VALUES
            ('L1', 'keep', 'pending', '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00'),
            (
                'missing',
                'drop',
                'pending',
                '2026-06-03T12:00:02+00:00',
                '2026-06-03T12:00:02+00:00'
            );
        PRAGMA user_version = 5;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        async with migrated._conn.execute("PRAGMA foreign_key_list(queued_messages)") as cur:
            fks = await cur.fetchall()
        assert any(row["table"] == "lanes" and row["from"] == "lane" for row in fks)
        assert await migrated.pending_message_count("L1") == 1
        with pytest.raises(aiosqlite.IntegrityError):
            await migrated.enqueue_message(lane="missing", text="nope")
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION
    finally:
        await migrated.close()


async def test_v5_migration_prunes_existing_orphan_lane_children(tmp_path: Path) -> None:
    db = tmp_path / "registry-v5-orphans.db"
    seeded = await Registry.open(db, now=_clock)
    await seeded.close()

    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        INSERT INTO lane_sync_sources (lane, state, last_synced_at)
        VALUES ('missing-sync', 'metadata', '2026-06-03T12:00:01+00:00');
        INSERT INTO lane_snapshots (lane, display_name, transcript_partial)
        VALUES ('missing-snapshot', 'Orphan', 1);
        INSERT INTO lane_model_settings (lane, model, service_tier_source, updated_at)
        VALUES ('missing-model', 'gpt-5.5', 'observed', '2026-06-03T12:00:01+00:00');
        INSERT INTO queued_messages (lane, text, status, created_at, updated_at)
        VALUES (
            'missing-queue',
            'lost',
            'pending',
            '2026-06-03T12:00:01+00:00',
            '2026-06-03T12:00:01+00:00'
        );
        PRAGMA user_version = 5;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        assert await migrated.get_lane_sync("missing-sync") is None
        assert await migrated.get_lane_model_settings("missing-model") is None
        async with migrated._conn.execute("SELECT COUNT(*) AS count FROM queued_messages") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row["count"]) == 0
        async with migrated._conn.execute("PRAGMA foreign_key_check") as cur:
            rows = await cur.fetchall()
        assert rows == []
    finally:
        await migrated.close()
