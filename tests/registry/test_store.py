"""Unit tests for the aiosqlite registry store (in-memory, fixed clock)."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import NotFoundError
from outfitter.dispatch.registry.models import (
    SERVER_REQUEST_TEXT_LIMIT,
    LaneRuntimeSettings,
    LaneSync,
    PermissionProfileEntry,
    ProviderThreadObservation,
    QueuedMessage,
    Subscription,
)
from outfitter.dispatch.registry.refs import BASE58BTC_ALPHABET, codex_ref_payload
from outfitter.dispatch.registry.store import SCHEMA_VERSION, Registry
from tests.fixtures.registry.builders import (
    lane_model_settings,
    lane_runtime_settings,
    lane_runtime_state,
    message_receipt,
    model_catalog_entry,
    provider_capacity_observation,
    provider_event,
    provider_thread_observation,
    server_request,
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


async def test_v20_queue_migration_preserves_pending_and_restart_rows(tmp_path: Path) -> None:
    db = tmp_path / "registry-v20.db"
    seeded = await Registry.open(db, now=_clock)
    await seeded.add_lane(id="L1", handle="@one", source="own", status="idle")
    await seeded.close()

    conn = await aiosqlite.connect(db)
    await conn.execute("ALTER TABLE queued_messages DROP COLUMN content")
    await conn.executemany(
        "INSERT INTO queued_messages (lane, text, status, created_at, updated_at) "
        "VALUES ('L1', ?, ?, ?, ?)",
        [
            ("pending", "pending", _clock().isoformat(), _clock().isoformat()),
            ("in-flight", "sending", _clock().isoformat(), _clock().isoformat()),
        ],
    )
    await conn.execute("PRAGMA user_version = 20")
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    assert (await migrated.get_queued_message(1)).content == []
    assert (await migrated.get_queued_message(2)).status == "sending"
    assert await migrated.reset_sending_messages() == 1
    await migrated.close()

    reopened = await Registry.open(db, now=_clock)
    try:
        assert (await reopened.get_queued_message(1)).status == "pending"
        assert (await reopened.get_queued_message(2)).status == "pending"
        rich = await reopened.enqueue_message(
            lane="L1",
            text="",
            content=[{"type": "image", "url": "https://example.com/a.png"}],
        )
        assert rich.content == [{"type": "image", "url": "https://example.com/a.png"}]
    finally:
        await reopened.close()


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


async def test_permission_profile_catalog_is_scoped_and_replaced(store: Registry) -> None:
    first = PermissionProfileEntry(
        id=":workspace",
        cwd="/work/a",
        description="Write files",
        allowed=True,
        first_seen_at=store.now_iso(),
        last_seen_at=store.now_iso(),
    )
    await store.replace_permission_profiles("/work/a", [first])
    await store.replace_permission_profiles(
        "/work/b", [first.model_copy(update={"cwd": "/work/b", "allowed": False})]
    )
    refreshed = first.model_copy(
        update={"description": "Workspace access", "last_seen_at": "2026-06-03T12:05:00+00:00"}
    )
    await store.replace_permission_profiles("/work/a", [refreshed])

    assert await store.list_permission_profiles(cwd="/work/a") == [refreshed]
    scoped = await store.get_permission_profile(":workspace", cwd="/work/b")
    assert scoped is not None and scoped.allowed is False
    await store.replace_permission_profiles("/work/a", [])
    assert await store.list_permission_profiles(cwd="/work/a") == []


async def test_v20_migration_adds_permission_catalog_and_runtime_profile(
    tmp_path: Path,
) -> None:
    db = tmp_path / "registry-v19.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lane_runtime_settings (
            lane TEXT PRIMARY KEY,
            sandbox TEXT,
            approval_policy TEXT,
            approvals_reviewer TEXT,
            effort TEXT,
            summary TEXT,
            model TEXT,
            service_tier TEXT,
            output_schema TEXT,
            personality TEXT,
            updated_at TEXT NOT NULL
        );
        PRAGMA user_version = 19;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        async with migrated._conn.execute("PRAGMA table_info(lane_runtime_settings)") as cur:
            runtime_columns = {str(row["name"]) for row in await cur.fetchall()}
        async with migrated._conn.execute("PRAGMA table_info(permission_profiles)") as cur:
            catalog_columns = {str(row["name"]) for row in await cur.fetchall()}
        assert "permission_profile" in runtime_columns
        assert {"id", "cwd", "allowed", "last_seen_at"} <= catalog_columns
    finally:
        await migrated.close()


async def test_v13_migration_adds_model_capability_columns(tmp_path: Path) -> None:
    db = tmp_path / "registry-v12-model-catalog.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE model_catalog (
            id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'openai',
            display_name TEXT,
            description TEXT,
            is_default INTEGER,
            hidden INTEGER,
            default_reasoning_effort TEXT,
            supported_reasoning_efforts TEXT NOT NULL DEFAULT '[]',
            default_service_tier TEXT,
            service_tiers TEXT NOT NULL DEFAULT '[]',
            additional_speed_tiers TEXT NOT NULL DEFAULT '[]',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'app-server',
            PRIMARY KEY(provider, id)
        );
        INSERT INTO model_catalog (
            id, provider, supported_reasoning_efforts, service_tiers,
            additional_speed_tiers, first_seen_at, last_seen_at
        ) VALUES ('legacy', 'openai', '["low"]', '[]', '[]', 'before', 'before');
        PRAGMA user_version = 12;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        model = await migrated.get_model_catalog_entry("legacy")
        assert model is not None
        assert model.input_modalities == []
        assert model.supports_personality is None
        assert model.upgrade is None
        async with migrated._conn.execute("PRAGMA table_info(model_catalog)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        assert {"input_modalities", "supports_personality", "upgrade"} <= columns
    finally:
        await migrated.close()


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

    matches, scanned = await store.search_thread_items(
        query="PYTEST",
        lanes={"L1"},
        limit=2,
        max_scan=10,
    )
    assert scanned == 3
    assert [found.item_id for found in matches] == [newer.item_id, item.item_id]

    filtered, filtered_scanned = await store.query_thread_items(
        lanes={"L1"},
        tool="bash",
        file="README.md",
        item_type="tool",
        raw_retained=True,
        limit=5,
        max_scan=10,
    )
    assert filtered_scanned == 1
    assert [found.item_id for found in filtered] == [item.item_id]

    await store.upsert_thread_item(
        thread_item(item_id="item-src", position=3),
        refs=[thread_item_ref(item_id="item-src", ref_type="file", ref_value="src/app.py")],
    )
    await store.upsert_thread_item(
        thread_item(item_id="item-docs-src", position=4),
        refs=[
            thread_item_ref(
                item_id="item-docs-src",
                ref_type="file",
                ref_value="docs/src/app.py",
            )
        ],
    )
    under_src, under_src_scanned = await store.query_thread_items(
        lanes={"L1"},
        file_under="src",
        limit=10,
        max_scan=10,
    )
    assert under_src_scanned == 1
    assert [found.item_id for found in under_src] == ["item-src"]

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


async def test_thread_item_canonical_fields_roundtrip_query_and_upsert(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@lane", source="own", status="idle")
    payload = {"type": "mcpToolCall", "raw": {"retained": True}}
    original = thread_item(
        phase="analysis",
        status="failed",
        server="linear",
        command="fetch DIS-44",
        cwd="/work/dispatch",
        error="request failed",
        duration_ms=321,
        arguments={"id": "DIS-44", "flags": [True, False], "limit": 5},
        success=False,
        agent_nickname="review-agent",
        agent_role="reviewer",
    ).model_copy(update={"payload": payload})

    saved = await store.upsert_thread_item(original)

    assert saved == original
    matches, scanned = await store.query_thread_items(
        lanes={"L1"},
        tool_server="linear",
        tool_status="failed",
        errored=True,
        arg_key="flags",
    )
    assert scanned == 1
    assert matches == [original]

    replacement = original.model_copy(
        update={
            "phase": "final",
            "status": "completed",
            "error": None,
            "duration_ms": 654,
            "arguments": ["DIS-44", {"dry_run": False}],
            "success": True,
            "agent_nickname": None,
            "agent_role": None,
            "inserted_at": "2026-06-11T12:05:00+00:00",
        }
    )
    updated = await store.upsert_thread_item(replacement)

    expected = replacement.model_copy(
        update={
            "error": original.error,
            "agent_nickname": original.agent_nickname,
            "agent_role": original.agent_role,
            "inserted_at": original.inserted_at,
        }
    )
    assert updated == expected
    assert updated.payload == payload
    assert updated.success is True

    sparse = replacement.model_copy(
        update={
            "role": None,
            "phase": None,
            "status": "inProgress",
            "text": None,
            "tool": None,
            "server": None,
            "command": None,
            "cwd": None,
            "error": None,
            "duration_ms": None,
            "arguments": None,
            "success": None,
            "agent_nickname": None,
            "agent_role": None,
            "created_at": None,
            "position": None,
            "inserted_at": "2026-06-11T12:10:00+00:00",
            "payload": None,
            "raw_retained": False,
        }
    )
    assert await store.upsert_thread_item(sparse) == expected


async def test_v14_registry_migration_adds_canonical_thread_item_columns(
    tmp_path: Path,
) -> None:
    db = tmp_path / "registry-v14.db"
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(
            """
            CREATE TABLE thread_items (
                provider TEXT NOT NULL,
                provider_thread_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                lane TEXT,
                turn_id TEXT,
                item_type TEXT NOT NULL,
                role TEXT,
                text TEXT,
                tool TEXT,
                created_at TEXT,
                position INTEGER,
                inserted_at TEXT NOT NULL,
                payload TEXT,
                raw_retained INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(provider, provider_thread_id, item_id)
            );
            INSERT INTO thread_items (
                provider, provider_thread_id, item_id, item_type, text, inserted_at,
                payload, raw_retained
            ) VALUES (
                'codex', 'thread-legacy', 'item-legacy', 'toolCall', 'legacy item',
                '2026-07-10T12:00:00+00:00', '{"type":"toolCall"}', 1
            );
            PRAGMA user_version = 14;
            """
        )
        await conn.commit()

    migrated = await Registry.open(db, now=_clock)
    try:
        item = await migrated.get_thread_item("codex", "thread-legacy", "item-legacy")
        assert item.phase is None
        assert item.status is None
        assert item.arguments is None
        assert item.success is None
        assert item.payload == {"type": "toolCall"}
        assert item.raw_retained is True
        async with migrated._conn.execute("PRAGMA table_info(thread_items)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        assert {
            "phase",
            "status",
            "server",
            "command",
            "cwd",
            "error",
            "duration_ms",
            "arguments",
            "success",
            "agent_nickname",
            "agent_role",
        } <= columns
    finally:
        await migrated.close()

    reopened = await Registry.open(db, now=_clock)
    try:
        item = await reopened.get_thread_item("codex", "thread-legacy", "item-legacy")
        assert item.payload == {"type": "toolCall"}
        assert item.arguments is None
        async with reopened._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION
    finally:
        await reopened.close()


async def test_server_requests_roundtrip_dedupe_and_preserve_int_string_ids(
    store: Registry,
) -> None:
    await store.add_lane(id="L1", handle="@lane", source="own")
    numeric = await store.observe_server_request(server_request(request_id=1))
    duplicate = await store.observe_server_request(
        server_request(request_id=1, received_at="2026-06-11T12:01:00+00:00")
    )
    string = await store.observe_server_request(server_request(request_id="1"))

    assert duplicate == numeric
    pending = await store.list_pending_server_requests()
    assert {request.request_id for request in pending} == {1, "1"}
    all_requests = await store.list_server_requests(state=None)
    assert {request.request_id for request in all_requests} == {1, "1"}
    assert (
        await store.get_server_request(
            provider="codex",
            provider_session_id="app-server-1",
            provider_thread_id="thread-1",
            request_id="1",
        )
        == string
    )


async def test_server_request_observation_reports_atomic_insert_winner(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@lane", source="own")
    first, duplicate = await asyncio.gather(
        store.observe_server_request_once(server_request(request_id=7)),
        store.observe_server_request_once(server_request(request_id=7)),
    )

    assert first.request.id == duplicate.request.id
    assert {first.inserted, duplicate.inserted} == {True, False}


async def test_server_requests_support_threadless_recovery_and_terminal_claims(
    store: Registry,
) -> None:
    observed = await store.observe_server_request(
        server_request(provider_thread_id=None, lane=None, request_id="approval-1")
    )
    assert observed.id is not None

    assert await store.list_pending_server_requests() == [observed]
    human_claim, timeout_claim = await asyncio.gather(
        store.claim_server_request_by_id(observed.id),
        store.claim_server_request_by_id(observed.id),
    )

    claim = human_claim or timeout_claim
    assert claim is not None
    assert claim.state == "responding"
    assert (human_claim is None) != (timeout_claim is None)
    assert await store.list_server_requests(state="responding") == [claim]
    assert await store.list_pending_server_requests() == []
    final = await store.finalize_server_request_by_id(
        observed.id,
        state="responded" if human_claim is not None else "timed_out",
        response_summary="accepted by operator" if human_claim is not None else None,
        error="deadline elapsed" if timeout_claim is not None else None,
    )
    assert final is not None
    assert final.resolved_at == store.now_iso()
    assert final.state in {"responded", "timed_out"}
    assert (
        await store.observe_server_request(
            server_request(provider_thread_id=None, lane=None, request_id="approval-1")
        )
    ) == final
    assert (await store.get_server_request_by_id(observed.id)) == final


async def test_server_request_claim_bounds_terminal_summary_and_error(store: Registry) -> None:
    await store.observe_server_request(server_request(lane=None))
    request = await store.get_server_request(
        provider="codex",
        provider_session_id="app-server-1",
        provider_thread_id="thread-1",
        request_id=1,
    )
    assert request is not None
    assert request.id is not None
    assert (
        await store.finalize_server_request_by_id(request.id, state="failed", error="not claimed")
    ) is None
    claimed = await store.claim_server_request_by_id(request.id)
    assert claimed is not None
    assert claimed.state == "responding"
    finalized = await store.finalize_server_request_by_id(
        request.id,
        state="failed",
        response_summary="s" * (SERVER_REQUEST_TEXT_LIMIT + 1),
        error="e" * (SERVER_REQUEST_TEXT_LIMIT + 1),
    )

    assert finalized is not None
    assert finalized.response_summary == "s" * SERVER_REQUEST_TEXT_LIMIT
    assert finalized.error == "e" * SERVER_REQUEST_TEXT_LIMIT
    assert await store.finalize_server_request_by_id(request.id, state="responded") is None


async def test_server_request_pending_rows_survive_reopen(tmp_path: Path) -> None:
    db = tmp_path / "pending-requests.sqlite3"
    original = await Registry.open(db, now=_clock)
    await original.observe_server_request(server_request(provider_thread_id=None, lane=None))
    await original.close()

    recovered = await Registry.open(db, now=_clock)
    try:
        pending = await recovered.list_pending_server_requests()
        assert [(request.provider_thread_id, request.request_id) for request in pending] == [
            (None, 1)
        ]
    finally:
        await recovered.close()


async def test_server_request_restart_reuses_wire_ids_and_fails_old_open_rows(
    tmp_path: Path,
) -> None:
    db = tmp_path / "request-restart.sqlite3"
    original = await Registry.open(db, now=_clock)
    terminal = await original.observe_server_request(
        server_request(provider_session_id="app-server-1", lane=None, request_id=1)
    )
    assert terminal.id is not None
    assert await original.claim_server_request_by_id(terminal.id) is not None
    assert (
        await original.finalize_server_request_by_id(terminal.id, state="responded")
    ) is not None
    old_pending = await original.observe_server_request(
        server_request(provider_session_id="app-server-1", lane=None, request_id=2)
    )
    old_responding = await original.observe_server_request(
        server_request(provider_session_id="app-server-1", lane=None, request_id=3)
    )
    assert old_responding.id is not None
    assert await original.claim_server_request_by_id(old_responding.id) is not None
    await original.close()

    recovered = await Registry.open(db, now=_clock)
    try:
        reused = await recovered.observe_server_request(
            server_request(provider_session_id="app-server-2", lane=None, request_id=1)
        )
        failed = await recovered.fail_open_server_requests_except_session(
            "app-server-2", error="connection restarted"
        )

        assert failed == 2
        assert reused.state == "pending"
        assert (await recovered.get_server_request_by_id(terminal.id)).state == "responded"  # type: ignore[union-attr]
        assert old_pending.id is not None
        assert (await recovered.get_server_request_by_id(old_pending.id)).state == "failed"  # type: ignore[union-attr]
        assert (await recovered.get_server_request_by_id(old_responding.id)).state == "failed"  # type: ignore[union-attr]
        assert await recovered.list_pending_server_requests(provider_session_id="app-server-2") == [
            reused
        ]
    finally:
        await recovered.close()


async def test_v13_registry_migration_adds_server_requests(tmp_path: Path) -> None:
    db = tmp_path / "registry-v13.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA user_version = 13")
        await conn.commit()

    migrated = await Registry.open(db, now=_clock)
    try:
        saved = await migrated.observe_server_request(
            server_request(provider_thread_id=None, lane=None)
        )
        assert saved.provider_thread_id is None
        async with migrated._conn.execute("PRAGMA table_info(server_requests)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        assert {
            "id",
            "provider_session_id",
            "provider_thread_key",
            "request_id_json",
            "response_summary",
            "error",
        } <= columns
    finally:
        await migrated.close()


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
    await store.upsert_thread_history_snapshot(
        turns=[turn],
        items=[
            (
                item.model_copy(update={"inserted_at": "2026-06-11T12:05:00+00:00"}),
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
    assert (
        await store.find_thread_item(stale.provider, stale.provider_thread_id, stale.item_id)
        is None
    )
    assert [found.turn_id for found in await store.list_thread_turns(lane="L1")] == ["turn-1"]
    listed_items = await store.list_thread_items(lane="L1")
    assert [found.item_id for found in listed_items] == ["item-1"]
    assert listed_items[0].inserted_at == item.inserted_at
    refs_by_item = await store.list_thread_item_refs_many([item])
    key = (item.provider, item.provider_thread_id, item.item_id)
    assert [(ref.ref_type, ref.ref_value) for ref in refs_by_item[key]] == [
        ("file", "README.md"),
        ("tool", "bash"),
    ]
    stats = await store.get_thread_history_summary_stats(lane="L1")
    assert stats.turns == 1
    assert stats.items == 1
    assert stats.tool_calls == 1
    assert [tool.tool for tool in stats.tools] == ["bash"]
    assert [(file.path, file.count) for file in stats.files] == [("README.md", 1)]


async def test_list_thread_item_refs_many_keeps_same_item_ids_separate(
    store: Registry,
) -> None:
    await store.add_lane(id="L1", handle="@lane", source="own", status="idle")
    first = await store.upsert_thread_item(
        thread_item(provider_thread_id="thread-1", item_id="shared"),
        refs=[
            thread_item_ref(
                provider_thread_id="thread-1",
                item_id="shared",
                ref_value="bash",
            )
        ],
    )
    second = await store.upsert_thread_item(
        thread_item(provider_thread_id="thread-2", item_id="shared"),
        refs=[
            thread_item_ref(
                provider_thread_id="thread-2",
                item_id="shared",
                ref_value="linear",
            )
        ],
    )

    refs = await store.list_thread_item_refs_many([first, second])

    assert set(refs) == {
        ("codex", "thread-1", "shared"),
        ("codex", "thread-2", "shared"),
    }
    assert [ref.ref_value for ref in refs[("codex", "thread-1", "shared")]] == ["bash"]
    assert [ref.ref_value for ref in refs[("codex", "thread-2", "shared")]] == ["linear"]


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


async def test_provider_event_foreign_key_errors_are_not_ignored(store: Registry) -> None:
    with pytest.raises(aiosqlite.IntegrityError):
        await store.record_provider_event(provider_event(lane="missing"))


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
            next_offset=256,
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
            history_source="app-server-turns",
            history_cursor="older-page",
            history_backwards_cursor="newer-page",
            history_recent_cursor="recent-page",
            history_pending_backwards_cursor="pending-newer-page",
            history_item_turn_id="turn-5",
            history_item_turn_cursor="turn-page",
            history_item_turn_direction="asc",
            history_item_cursor="item-page",
            history_cursor_guard="00ff",
            history_capability="supported",
            observation_enabled=True,
            pages_scanned=1,
            turns_indexed=5,
            items_indexed=20,
            scanned_bytes=4096,
            duration_ms=12,
            truncated=True,
        )
    )

    assert saved.last_synced_at == _clock().isoformat()
    assert saved.display_name == "Desktop"
    assert saved.source_size == 3
    assert saved.next_offset == 256
    assert saved.history_cursor == "older-page"
    assert saved.history_item_cursor == "item-page"
    assert saved.history_recent_cursor == "recent-page"
    assert saved.observation_enabled is True
    assert saved.history_item_turn_cursor == "turn-page"
    assert saved.history_item_turn_direction == "asc"
    assert saved.history_cursor_guard == "00ff"
    assert saved.turns_indexed == 5
    assert saved.truncated is True

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


async def test_lane_sync_transaction_serializes_with_live_event_writes(
    store: Registry,
) -> None:
    await store.add_lane(id="L1", handle="@one", source="own")

    async def sync_once(index: int) -> None:
        await store.upsert_lane_sync(LaneSync(lane="L1", state="partial", pages_scanned=index))

    async def event_once(index: int) -> None:
        await store.touch_lane_event("L1")
        await store.log_action("event", lane="L1", detail=str(index))

    await asyncio.gather(
        *(sync_once(index) for index in range(20)),
        *(event_once(index) for index in range(20)),
    )

    assert await store.get_lane_sync("L1") is not None
    assert len(await store.recent_actions(limit=25)) == 20


async def test_unrelated_write_cannot_commit_or_enter_active_transaction(
    store: Registry,
) -> None:
    await store.add_lane(id="L1", handle="@one", source="own")
    queued: asyncio.Task[QueuedMessage] | None = None

    with pytest.raises(RuntimeError, match="rollback owner"):
        async with store._transaction():
            await store._insert_action_log("should-rollback", lane="L1")
            queued = asyncio.create_task(store.enqueue_message(lane="L1", text="later"))
            await asyncio.sleep(0.01)
            assert queued.done() is False
            raise RuntimeError("rollback owner")

    assert queued is not None
    message = await queued
    assert message.text == "later"
    assert all(action.op != "should-rollback" for action in await store.recent_actions())


async def test_unrelated_read_cannot_observe_active_transaction(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@one", source="own")
    inserted = asyncio.Event()
    release = asyncio.Event()

    async def rollback_owner() -> None:
        with pytest.raises(RuntimeError, match="rollback owner"):
            async with store._transaction():
                await store._insert_action_log("should-rollback", lane="L1")
                inserted.set()
                await release.wait()
                raise RuntimeError("rollback owner")

    owner = asyncio.create_task(rollback_owner())
    await inserted.wait()
    reader = asyncio.create_task(store.recent_actions())
    await asyncio.sleep(0.01)
    assert reader.done() is False

    release.set()
    await owner
    assert all(action.op != "should-rollback" for action in await reader)


def test_all_public_registry_access_is_serialized() -> None:
    unguarded = [
        name
        for name, member in Registry.__dict__.items()
        if not name.startswith("_")
        and name != "open"
        and inspect.iscoroutinefunction(member)
        and not getattr(member, "__registry_serialized__", False)
    ]

    assert unguarded == []


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


async def test_v16_migration_adds_independent_provider_threads_table(tmp_path: Path) -> None:
    db = tmp_path / "registry-v15.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lanes (id TEXT PRIMARY KEY);
        PRAGMA user_version = 15;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        async with migrated._conn.execute("PRAGMA table_info(provider_threads)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        assert {
            "provider",
            "provider_thread_id",
            "parent_thread_id",
            "forked_from_id",
            "lifecycle_state",
            "first_seen_at",
            "last_seen_at",
            "archived_at",
            "deleted_at",
        } <= columns
        async with migrated._conn.execute("PRAGMA foreign_key_list(provider_threads)") as cur:
            assert await cur.fetchall() == []
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION
    finally:
        await migrated.close()


async def test_v17_migration_adds_replace_in_place_provider_capacity_table(
    tmp_path: Path,
) -> None:
    db = tmp_path / "registry-v16.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lanes (id TEXT PRIMARY KEY);
        PRAGMA user_version = 16;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        async with migrated._conn.execute(
            "PRAGMA table_info(provider_capacity_observations)"
        ) as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        assert {
            "provider",
            "host_scope",
            "config_scope",
            "state",
            "account_fingerprint",
            "observed_at",
            "payload",
        } <= columns
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION == 21
    finally:
        await migrated.close()


async def test_v18_migration_adds_bounded_history_progress_columns(tmp_path: Path) -> None:
    db = tmp_path / "registry-v17.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(
        """
        CREATE TABLE lanes (id TEXT PRIMARY KEY);
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
        PRAGMA user_version = 17;
        """
    )
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        async with migrated._conn.execute("PRAGMA table_info(lane_sync_sources)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        assert {
            "history_cursor",
            "next_offset",
            "history_backwards_cursor",
            "history_recent_cursor",
            "history_pending_backwards_cursor",
            "history_item_turn_id",
            "history_item_turn_cursor",
            "history_item_turn_direction",
            "history_item_cursor",
            "history_cursor_guard",
            "history_complete",
            "history_capability",
            "observation_enabled",
            "pages_scanned",
            "turns_indexed",
            "items_indexed",
            "unchanged_skipped",
            "scanned_bytes",
            "duration_ms",
            "truncated",
        } <= columns
    finally:
        await migrated.close()


async def test_provider_capacity_observation_replaces_current_scope_without_duplicates(
    store: Registry,
) -> None:
    first = provider_capacity_observation(observed_at="2026-07-10T12:00:00+00:00")
    second = provider_capacity_observation(observed_at="2026-07-10T12:05:00+00:00").model_copy(
        update={"plan": "team"}
    )

    await store.upsert_provider_capacity_observation(first)
    saved = await store.upsert_provider_capacity_observation(second)

    assert saved.plan == "team"
    assert saved.observed_at == "2026-07-10T12:05:00+00:00"
    assert await store.list_provider_capacity_observations(provider="codex") == [saved]
    async with store._conn.execute(
        "SELECT COUNT(*) AS count, payload FROM provider_capacity_observations"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and int(row["count"]) == 1
    payload = str(row["payload"])
    assert "agent@example.com" not in payload
    assert "opaque-credit-1" not in payload


async def test_provider_thread_sparse_upsert_preserves_identity_lineage_and_lifecycle(
    store: Registry,
) -> None:
    archived_at = "2026-06-03T12:01:00+00:00"
    observed = provider_thread_observation(
        provider_thread_id="child",
        parent_thread_id="parent",
        forked_from_id="fork-origin",
        lifecycle_state="archived",
        observed_at=archived_at,
    )
    initial = await store.upsert_provider_thread(observed)
    assert await store.upsert_provider_thread(observed) == initial

    saved = await store.upsert_provider_thread(
        ProviderThreadObservation(
            provider="codex",
            provider_thread_id="child",
            observed_at="2026-06-03T12:02:00+00:00",
        )
    )

    assert saved.session_id == "session-1"
    assert saved.parent_thread_id == "parent"
    assert saved.forked_from_id == "fork-origin"
    assert saved.agent_nickname == "worker"
    assert saved.relationship_source == "history"
    assert saved.lifecycle_state == "archived"
    assert saved.first_seen_at == archived_at
    assert saved.last_seen_at == "2026-06-03T12:02:00+00:00"
    assert saved.archived_at == archived_at


async def test_provider_thread_batch_upsert_returns_each_observation(store: Registry) -> None:
    saved = await store.upsert_provider_threads(
        [
            provider_thread_observation(provider_thread_id="one"),
            provider_thread_observation(provider_thread_id="two", parent_thread_id="one"),
        ]
    )

    assert [thread.provider_thread_id for thread in saved] == ["one", "two"]
    assert saved[1].parent_thread_id == "one"


async def test_provider_thread_batch_upsert_rolls_back_as_one_transaction(
    store: Registry,
) -> None:
    await store._conn.executescript(
        """
        CREATE TRIGGER reject_bad_provider_thread
        BEFORE INSERT ON provider_threads
        WHEN NEW.provider_thread_id = 'bad'
        BEGIN
            SELECT RAISE(FAIL, 'rejected provider thread');
        END;
        """
    )
    await store._conn.commit()

    with pytest.raises(aiosqlite.IntegrityError, match="rejected provider thread"):
        await store.upsert_provider_threads(
            [
                provider_thread_observation(provider_thread_id="good"),
                provider_thread_observation(provider_thread_id="bad"),
            ]
        )

    assert await store.get_provider_thread("codex", "good") is None


async def test_provider_thread_lifecycle_tombstones_outlive_lane_deletion(store: Registry) -> None:
    await store.add_lane(id="thread-1", handle="@one", source="own")
    await store.upsert_provider_thread(provider_thread_observation())
    archived = await store.mark_provider_thread_state(
        "codex", "thread-1", "archived", observed_at="2026-06-03T12:01:00+00:00"
    )
    deleted = await store.mark_provider_thread_state(
        "codex", "thread-1", "deleted", observed_at="2026-06-03T12:02:00+00:00"
    )
    await store._conn.execute("DELETE FROM lanes WHERE id = ?", ("thread-1",))
    await store._conn.commit()

    assert archived.archived_at == "2026-06-03T12:01:00+00:00"
    assert deleted.lifecycle_state == "deleted"
    assert deleted.archived_at == "2026-06-03T12:01:00+00:00"
    assert deleted.deleted_at == "2026-06-03T12:02:00+00:00"
    assert await store.get_provider_thread("codex", "thread-1") == deleted


async def test_provider_thread_topology_completes_after_late_parent_discovery(
    store: Registry,
) -> None:
    await store.upsert_provider_thread(
        provider_thread_observation(provider_thread_id="child", parent_thread_id="parent")
    )

    missing = await store.get_provider_thread_topology("codex", "child")
    assert missing.complete is False
    assert missing.roots["child"] is None
    assert missing.missing_thread_ids == ["parent"]

    await store.upsert_provider_thread(provider_thread_observation(provider_thread_id="parent"))
    discovered = await store.get_provider_thread_topology("codex", "child")
    assert discovered.complete is True
    assert [node.thread.provider_thread_id for node in discovered.parent_ancestry["child"]] == [
        "parent"
    ]
    assert discovered.roots["child"] is not None
    assert discovered.roots["child"].thread.provider_thread_id == "parent"


async def test_provider_thread_topology_preserves_parent_cycles(store: Registry) -> None:
    await store.upsert_provider_thread(
        provider_thread_observation(provider_thread_id="a", parent_thread_id="b")
    )
    await store.upsert_provider_thread(
        provider_thread_observation(provider_thread_id="b", parent_thread_id="a")
    )

    topology = await store.get_provider_thread_topology("codex", "a")

    assert topology.cycle_detected is True
    assert topology.complete is False
    assert topology.roots["a"] is None
    saved = await store.get_provider_thread("codex", "a")
    assert saved is not None
    assert saved.parent_thread_id == "b"


async def test_provider_thread_topology_reports_self_links_as_cycles(store: Registry) -> None:
    await store.upsert_provider_thread(
        provider_thread_observation(provider_thread_id="self", parent_thread_id="self")
    )

    topology = await store.get_provider_thread_topology("codex", "self")

    assert topology.cycle_detected is True
    assert topology.complete is False
    assert topology.roots["self"] is None


async def test_provider_thread_topology_separates_parent_descendants_and_forks(
    store: Registry,
) -> None:
    root_lane = await store.add_lane(id="root", handle="@root", source="own")
    await store.update_lane_status("root", "archived")
    await store.upsert_provider_thread(provider_thread_observation(provider_thread_id="root"))
    await store.upsert_provider_thread(
        provider_thread_observation(provider_thread_id="child", parent_thread_id="root")
    )
    await store.upsert_provider_thread(
        provider_thread_observation(provider_thread_id="grand", parent_thread_id="child")
    )
    await store.upsert_provider_thread(
        provider_thread_observation(provider_thread_id="fork", forked_from_id="root")
    )

    topology = await store.get_provider_thread_topology("codex", ["root", "child", "fork"])

    assert topology.complete is True
    assert topology.roots["child"] is not None
    assert topology.roots["child"].thread.provider_thread_id == "root"
    assert [node.thread.provider_thread_id for node in topology.children["root"]] == ["child"]
    assert [node.thread.provider_thread_id for node in topology.descendants["root"]] == [
        "child",
        "grand",
    ]
    assert [node.thread.provider_thread_id for node in topology.forks["root"]] == ["fork"]
    assert topology.fork_origins["root"] is None
    assert topology.fork_origins["fork"] is not None
    assert topology.fork_origins["fork"].thread.provider_thread_id == "root"
    fork = next(node for node in topology.nodes if node.thread.provider_thread_id == "fork")
    assert fork not in topology.descendants["root"]
    root = next(node for node in topology.nodes if node.thread.provider_thread_id == "root")
    assert root.managed is True
    assert root.ref == root_lane.ref
    assert root.handle == "@root"
    assert root.lane_status == "archived"


async def test_provider_thread_topology_joins_current_handle_after_rename(
    store: Registry,
) -> None:
    lane = await store.add_lane(id="root", handle="@before", source="own")
    await store.upsert_provider_thread(provider_thread_observation(provider_thread_id="root"))

    await store.update_lane_handle(lane.id, "@after")
    topology = await store.get_provider_thread_topology("codex", lane.id)

    [node] = topology.nodes
    assert node.thread.provider_thread_id == lane.id
    assert node.ref == lane.ref
    assert node.handle == "@after"


async def test_provider_thread_topology_reports_nested_depth_and_node_truncation(
    store: Registry,
) -> None:
    for thread_id, parent_id in (
        ("root", None),
        ("child-a", "root"),
        ("child-b", "root"),
        ("grand", "child-a"),
    ):
        await store.upsert_provider_thread(
            provider_thread_observation(provider_thread_id=thread_id, parent_thread_id=parent_id)
        )

    depth_limited = await store.get_provider_thread_topology("codex", "root", max_depth=1)
    assert depth_limited.truncated is True
    assert depth_limited.complete is False
    assert [node.thread.provider_thread_id for node in depth_limited.descendants["root"]] == [
        "child-a",
        "child-b",
    ]

    node_limited = await store.get_provider_thread_topology("codex", "root", max_nodes=2)
    assert node_limited.truncated is True
    assert node_limited.complete is False
    assert len(node_limited.nodes) == 2
