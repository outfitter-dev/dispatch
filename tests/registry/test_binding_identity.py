"""Binding-scoped registry identity and v23 migration coverage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from outfitter.dispatch.contracts.errors import NotFoundError, ValidationError
from outfitter.dispatch.registry.models import (
    LaneRuntimeState,
    MessageReceipt,
    ProviderEvent,
    ProviderThreadObservation,
    ServerRequest,
    ThreadItem,
    ThreadItemRef,
    ThreadTurn,
)
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID, Registry

_NOW = "2026-09-12T12:00:00+00:00"


async def test_same_native_identity_is_isolated_by_binding(tmp_path: Path) -> None:
    store = await Registry.open(tmp_path / "registry.db")
    try:
        lane_a = await store.add_lane(
            id="dsp_lane_a",
            handle="@a",
            source="own",
            provider="claude",
            binding_id="profile-a",
            provider_session_id="native-shared",
        )
        lane_b = await store.add_lane(
            id="dsp_lane_b",
            handle="@b",
            source="own",
            provider="claude",
            binding_id="profile-b",
            provider_session_id="native-shared",
        )
        assert lane_a.ref_source == lane_b.ref_source == "1"

        for lane in (lane_a, lane_b):
            await store.upsert_provider_thread(
                ProviderThreadObservation(
                    provider="claude",
                    binding_id=lane.binding_id,
                    provider_thread_id="native-shared",
                    parent_thread_id="parent-shared",
                    lifecycle_state="active",
                    observed_at=_NOW,
                )
            )
            await store.upsert_provider_thread(
                ProviderThreadObservation(
                    provider="claude",
                    binding_id=lane.binding_id,
                    provider_thread_id="parent-shared",
                    lifecycle_state="active",
                    observed_at=_NOW,
                )
            )
            await store.record_provider_event(
                ProviderEvent(
                    provider="claude",
                    binding_id=lane.binding_id,
                    provider_thread_id="native-shared",
                    lane=lane.id,
                    event_type="turn.started",
                    provider_event_id="event-shared",
                    received_at=_NOW,
                )
            )
            await store.upsert_thread_turn(
                ThreadTurn(
                    provider="claude",
                    binding_id=lane.binding_id,
                    provider_thread_id="native-shared",
                    turn_id="turn-shared",
                    lane=lane.id,
                    updated_at=_NOW,
                )
            )
            item = ThreadItem(
                provider="claude",
                binding_id=lane.binding_id,
                provider_thread_id="native-shared",
                item_id="item-shared",
                lane=lane.id,
                item_type="message",
                text=lane.binding_id,
                inserted_at=_NOW,
            )
            await store.upsert_thread_item(
                item,
                refs=[
                    ThreadItemRef(
                        provider="claude",
                        binding_id=lane.binding_id,
                        provider_thread_id="native-shared",
                        item_id="item-shared",
                        ref_type="binding",
                        ref_value=lane.binding_id,
                    )
                ],
            )

        assert (
            await store.get_thread_item(
                "claude", "native-shared", "item-shared", binding_id="profile-a"
            )
        ).text == "profile-a"
        assert (
            await store.get_thread_item(
                "claude", "native-shared", "item-shared", binding_id="profile-b"
            )
        ).text == "profile-b"
        assert (
            len(
                await store.list_provider_events(
                    provider="claude",
                    binding_id="profile-a",
                    provider_thread_id="native-shared",
                )
            )
            == 1
        )
        topology_a = await store.get_provider_thread_topology(
            "claude", "native-shared", binding_id="profile-a"
        )
        topology_b = await store.get_provider_thread_topology(
            "claude", "native-shared", binding_id="profile-b"
        )
        assert topology_a.nodes[0].thread.binding_id == "profile-a"
        assert topology_b.nodes[0].thread.binding_id == "profile-b"
        assert {node.thread.provider_thread_id: node.managed for node in topology_a.nodes}[
            "native-shared"
        ] is True
        assert {node.thread.provider_thread_id: node.managed for node in topology_b.nodes}[
            "native-shared"
        ] is True

        with pytest.raises(sqlite3.IntegrityError):
            await store._conn.execute(
                "INSERT INTO thread_item_refs "
                "(provider, binding_id, provider_thread_id, item_id, ref_type, ref_value) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("claude", "missing", "native-shared", "item-shared", "tool", "bad"),
            )
    finally:
        await store.close()


async def test_dispatch_message_id_cannot_retarget_binding() -> None:
    store = await Registry.open()
    try:
        original = MessageReceipt(
            provider="codex",
            binding_id="profile-a",
            provider_thread_id="native",
            dispatch_message_id="dispatch-global",
            created_at=_NOW,
            updated_at=_NOW,
        )
        await store.upsert_message_receipt(original)
        with pytest.raises(RuntimeError, match="did not return a row"):
            await store.upsert_message_receipt(
                original.model_copy(update={"binding_id": "profile-b"})
            )
        saved = await store.find_message_receipt(
            provider="codex",
            binding_id="profile-a",
            dispatch_message_id="dispatch-global",
        )
        assert saved is not None and saved.binding_id == "profile-a"
    finally:
        await store.close()


async def test_provider_session_continuation_cannot_retarget_binding() -> None:
    store = await Registry.open()
    try:
        reserved = await store.add_lane(
            id="dsp_reserved",
            handle="@reserved",
            source="own",
            provider="claude",
            binding_id="profile-a",
        )
        assert reserved.provider_session_id is None
        continued = await store.update_lane_provider_session(
            reserved.id,
            provider="claude",
            binding_id="profile-a",
            provider_session_id="native-1",
        )
        assert continued.provider_session_id == "native-1"
        with pytest.raises(NotFoundError, match=r"no lane .* provider binding"):
            await store.update_lane_provider_session(
                reserved.id,
                provider="claude",
                binding_id="profile-b",
                provider_session_id="native-2",
            )
        assert (await store.get_lane(reserved.id)).provider_session_id == "native-1"
    finally:
        await store.close()


async def test_provider_session_collision_rolls_back_and_registry_remains_writable() -> None:
    store = await Registry.open()
    try:
        first = await store.add_lane(
            id="dsp_first",
            handle="@first",
            source="own",
            provider="claude",
            binding_id="profile-a",
            provider_session_id="native-shared",
        )
        second = await store.add_lane(
            id="dsp_second",
            handle="@second",
            source="own",
            provider="claude",
            binding_id="profile-a",
        )
        with pytest.raises(sqlite3.IntegrityError):
            await store.update_lane_provider_session(
                second.id,
                provider="claude",
                binding_id="profile-a",
                provider_session_id="native-shared",
            )
        assert store._conn.in_transaction is False
        assert (await store.get_lane(first.id)).provider_session_id == "native-shared"
        assert (await store.get_lane(second.id)).provider_session_id is None
        created = await store.add_lane(
            id="dsp_after",
            handle="@after",
            source="own",
            provider="claude",
            binding_id="profile-a",
        )
        assert created.id == "dsp_after"
    finally:
        await store.close()


async def test_default_codex_provider_session_cannot_diverge_from_stable_id() -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(id="codex-native", handle="@codex", source="own", status="idle")
        with pytest.raises(ValidationError, match="must equal the stable lane id"):
            await store.update_lane_provider_session(
                lane.id,
                provider="codex",
                binding_id=DEFAULT_CODEX_BINDING_ID,
                provider_session_id="replacement-native",
            )
        assert (await store.get_lane(lane.id)).provider_session_id == lane.id
    finally:
        await store.close()


async def test_new_lane_ids_keep_default_codex_and_other_provider_namespaces_disjoint() -> None:
    store = await Registry.open()
    try:
        with pytest.raises(ValueError, match="default-Codex lane"):
            await store.add_lane(
                id="codex-key",
                handle="@bad-codex",
                source="own",
                provider_session_id="different-native",
            )
        with pytest.raises(ValueError, match="opaque dsp_ Dispatch id"):
            await store.add_lane(
                id="codex-shaped-native",
                handle="@bad-other",
                source="own",
                provider="claude",
                binding_id="profile-a",
            )
    finally:
        await store.close()


async def test_server_request_recovery_is_binding_scoped() -> None:
    store = await Registry.open()
    try:
        for binding in ("profile-a", "profile-b"):
            await store.observe_server_request(
                ServerRequest(
                    binding_id=binding,
                    provider_session_id="generation-old",
                    provider_thread_id="native-shared",
                    request_id="request-shared",
                    method="item/tool/requestUserInput",
                    category="user_input",
                    received_at=_NOW,
                )
            )
        failed = await store.fail_open_server_requests_except_session(
            "generation-new", binding_id="profile-a"
        )
        assert failed == 1
        request_a = await store.get_server_request(
            provider="codex",
            binding_id="profile-a",
            provider_session_id="generation-old",
            provider_thread_id="native-shared",
            request_id="request-shared",
        )
        request_b = await store.get_server_request(
            provider="codex",
            binding_id="profile-b",
            provider_session_id="generation-old",
            provider_thread_id="native-shared",
            request_id="request-shared",
        )
        assert request_a is not None and request_a.state == "failed"
        assert request_b is not None and request_b.state == "pending"
    finally:
        await store.close()


async def _downgrade_seed_to_v23(path: Path, *, migration_collision: bool = False) -> None:
    store = await Registry.open(path)
    lane = await store.add_lane(id="codex-native", handle="@legacy", source="own")
    await store.upsert_provider_thread(
        ProviderThreadObservation(provider_thread_id=lane.id, observed_at=_NOW)
    )
    event = await store.record_provider_event(
        ProviderEvent(
            provider="codex",
            provider_thread_id=lane.id,
            lane=lane.id,
            event_type="turn.started",
            provider_event_id="legacy-event",
            received_at=_NOW,
        )
    )
    await store.upsert_thread_turn(
        ThreadTurn(
            provider="codex",
            provider_thread_id=lane.id,
            turn_id="legacy-turn",
            lane=lane.id,
            updated_at=_NOW,
        )
    )
    item = ThreadItem(
        provider="codex",
        provider_thread_id=lane.id,
        item_id="legacy-item",
        lane=lane.id,
        item_type="message",
        inserted_at=_NOW,
    )
    await store.upsert_thread_item(
        item,
        refs=[
            ThreadItemRef(
                provider="codex",
                provider_thread_id=lane.id,
                item_id=item.item_id,
                ref_type="tool",
                ref_value="legacy",
            )
        ],
    )
    receipt = await store.upsert_message_receipt(
        MessageReceipt(
            lane=lane.id,
            provider="codex",
            provider_thread_id=lane.id,
            dispatch_message_id="legacy-message",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    await store.upsert_lane_runtime_state(
        LaneRuntimeState(
            lane=lane.id,
            provider="codex",
            provider_thread_id=lane.id,
            updated_at=_NOW,
        )
    )
    request = await store.observe_server_request(
        ServerRequest(
            provider_session_id="legacy-generation",
            provider_thread_id=lane.id,
            lane=lane.id,
            request_id="legacy-request",
            method="item/tool/requestUserInput",
            category="user_input",
            received_at=_NOW,
        )
    )
    await store.close()

    tables = (
        "provider_threads",
        "provider_events",
        "thread_turns",
        "thread_items",
        "thread_item_refs",
        "message_receipts",
        "lane_runtime_state",
        "server_requests",
    )
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        snapshots = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
            for table in tables
        }
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE thread_item_refs")
        for table in tables:
            if table != "thread_item_refs":
                conn.execute(f"DROP TABLE {table}")
        schema_path = Path(__file__).parents[1] / "fixtures/registry/v23_binding_scope.sql"
        conn.executescript(schema_path.read_text())
        for table in tables:
            columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
            placeholders = ", ".join("?" for _ in columns)
            for row in snapshots[table]:
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    [row[column] for column in columns],
                )
        for table in ("provider_events", "message_receipts", "server_requests"):
            conn.execute("UPDATE sqlite_sequence SET seq = 42 WHERE name = ?", (table,))
        conn.execute("DROP INDEX idx_lanes_provider_session")
        conn.execute("ALTER TABLE lanes DROP COLUMN provider_session_id")
        conn.execute("ALTER TABLE lanes DROP COLUMN binding_id")
        conn.execute("ALTER TABLE lanes DROP COLUMN provider")
        if migration_collision:
            conn.execute("CREATE TABLE provider_events_v24 (sentinel TEXT)")
        conn.execute("PRAGMA user_version = 23")
        conn.commit()

    assert event.id is not None
    assert receipt.id is not None
    assert request.id is not None


async def test_v23_migration_preserves_keys_ids_and_foreign_keys(tmp_path: Path) -> None:
    path = tmp_path / "registry-v23.db"
    await _downgrade_seed_to_v23(path)
    with sqlite3.connect(path) as legacy:
        legacy_identity = legacy.execute(
            "SELECT id, ref, ref_source, ref_payload, ref_mixer FROM lanes"
        ).fetchone()

    store = await Registry.open(path)
    try:
        lane = await store.get_lane("codex-native")
        assert lane.provider == "codex"
        assert lane.binding_id == DEFAULT_CODEX_BINDING_ID
        assert lane.provider_session_id == lane.id
        assert lane.ref_source == "0"
        assert (
            lane.id,
            lane.ref,
            lane.ref_source,
            lane.ref_payload,
            lane.ref_mixer,
        ) == legacy_identity
        events = await store.list_provider_events(lane=lane.id)
        receipts = await store.list_message_receipts(lane=lane.id)
        requests = await store.list_server_requests(state=None)
        assert [(event.id, event.binding_id) for event in events] == [(1, DEFAULT_CODEX_BINDING_ID)]
        assert [(receipt.id, receipt.binding_id) for receipt in receipts] == [
            (1, DEFAULT_CODEX_BINDING_ID)
        ]
        assert [(request.id, request.binding_id) for request in requests] == [
            (1, DEFAULT_CODEX_BINDING_ID)
        ]
        next_event = await store.record_provider_event(
            ProviderEvent(
                provider="codex",
                provider_thread_id=lane.id,
                lane=lane.id,
                event_type="turn.completed",
                received_at=_NOW,
            )
        )
        next_receipt = await store.upsert_message_receipt(
            MessageReceipt(
                lane=lane.id,
                provider="codex",
                provider_thread_id=lane.id,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        next_request = await store.observe_server_request(
            ServerRequest(
                provider_session_id="next-generation",
                provider_thread_id=lane.id,
                lane=lane.id,
                request_id="next-request",
                method="item/tool/requestUserInput",
                category="user_input",
                received_at=_NOW,
            )
        )
        assert next_event.id == 43
        assert next_receipt.id == 43
        assert next_request.id == 43
        for table in (
            "provider_events",
            "thread_turns",
            "thread_items",
            "message_receipts",
            "lane_runtime_state",
            "server_requests",
        ):
            async with store._conn.execute(f"SELECT DISTINCT lane FROM {table}") as cur:
                assert {str(row[0]) for row in await cur.fetchall()} == {lane.id}
        async with store._conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []
        async with store._conn.execute("PRAGMA foreign_key_list(thread_item_refs)") as cur:
            foreign_keys = await cur.fetchall()
        assert {str(row["table"]) for row in foreign_keys} == {"thread_items"}
        assert {str(row["to"]) for row in foreign_keys} == {
            "provider",
            "binding_id",
            "provider_thread_id",
            "item_id",
        }
        async with store._conn.execute("PRAGMA user_version") as cur:
            assert int((await cur.fetchone())[0]) == 24  # type: ignore[index]
    finally:
        await store.close()


async def test_v23_migration_failure_rolls_back_schema_and_data(tmp_path: Path) -> None:
    path = tmp_path / "registry-v23-invalid.db"
    await _downgrade_seed_to_v23(path, migration_collision=True)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    store = Registry(conn, now=lambda: pytest.fail("clock should not be read"))
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        await store._ensure_binding_scope_v24()
    try:
        async with conn.execute("PRAGMA foreign_keys") as cur:
            assert int((await cur.fetchone())[0]) == 1  # type: ignore[index]
        async with conn.execute("PRAGMA user_version") as cur:
            assert int((await cur.fetchone())[0]) == 23  # type: ignore[index]
        async with conn.execute("PRAGMA table_info(provider_events)") as cur:
            assert "binding_id" not in {str(row["name"]) for row in await cur.fetchall()}
        async with conn.execute(
            "SELECT provider_event_id FROM provider_events WHERE id = 1"
        ) as cur:
            assert str((await cur.fetchone())[0]) == "legacy-event"  # type: ignore[index]
        async with conn.execute("SELECT sentinel FROM provider_events_v24") as cur:
            assert await cur.fetchall() == []
    finally:
        await store.close()

    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        await Registry.open(path)
    with sqlite3.connect(path) as repair:
        repair.execute("DROP TABLE provider_events_v24")
        repair.commit()
    reopened = await Registry.open(path)
    await reopened.close()
