"""Durable lane-creation reservation behavior."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import DeliveryConflictError, ValidationError
from outfitter.dispatch.registry.launch import LaneLaunch
from outfitter.dispatch.registry.models import Lane
from outfitter.dispatch.registry.store import SCHEMA_VERSION, Registry


def _clock() -> datetime:
    return datetime(2026, 9, 12, 16, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    registry = await Registry.open(now=_clock)
    try:
        yield registry
    finally:
        await registry.close()


async def _reserve(
    store: Registry,
    *,
    key: str | None = "create-1",
    submitted_payload: str = '{"name":"worker"}',
) -> tuple[LaneLaunch, Lane, bool]:
    return await store.reserve_lane_launch(
        key=key,
        submitted_payload=submitted_payload,
        request_payload='{"cwd":"/work","initial_text":"hello"}',
        handle="@worker",
        cwd="/work",
        provider="hermes",
        binding_id="hermes-default",
        generation="generation-1",
    )


async def test_reservation_atomically_creates_opaque_lane_and_launch(store: Registry) -> None:
    launch, lane, created = await _reserve(store)

    assert created is True
    assert lane.id.startswith("dsp_")
    assert len(lane.id) == len("dsp_") + 32
    assert lane.ref_source == "1"
    assert lane.provider_session_id is None
    assert lane.status == "unknown"
    assert launch.lane == lane.id
    assert launch.status == "reserved"
    assert launch.runtime_session_id is None
    assert launch.first_delivery_id is None
    assert await store.get_lane_launch(lane.id) == launch


async def test_lane_and_launch_insert_roll_back_together(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected launch insert failure")

    monkeypatch.setattr(Registry, "_insert_lane_launch", fail)
    with pytest.raises(RuntimeError, match="injected"):
        await _reserve(store)

    assert await store.list_lanes() == []


async def test_exact_concurrent_key_replay_converges_and_changed_input_conflicts(
    store: Registry,
) -> None:
    first, second = await asyncio.gather(_reserve(store), _reserve(store))

    assert first[0] == second[0]
    assert first[1] == second[1]
    assert sorted((first[2], second[2])) == [False, True]
    with pytest.raises(DeliveryConflictError, match="already bound"):
        await _reserve(store, submitted_payload='{"name":"different"}')
    assert len(await store.list_lanes()) == 1


async def test_creation_and_delivery_keys_share_one_local_namespace(store: Registry) -> None:
    _, lane, _ = await _reserve(store, key="creation-key")
    with pytest.raises(DeliveryConflictError, match="already bound"):
        await store.reserve_delivery(
            key="creation-key",
            lane=lane.id,
            mode="send",
            payload="{}",
            text="hello",
        )

    receipt, _ = await store.reserve_delivery(
        key="delivery-key",
        lane=lane.id,
        mode="send",
        payload="{}",
        text="hello",
    )
    assert receipt.key == "delivery-key"
    with pytest.raises(DeliveryConflictError, match="already bound"):
        await _reserve(store, key="delivery-key")


async def test_claim_is_exclusive_and_unfinished_claim_stays_held_after_reopen(
    tmp_path: Path,
) -> None:
    db = tmp_path / "registry.db"
    store = await Registry.open(db, now=_clock)
    launch, _, _ = await _reserve(store)
    assert await store.claim_lane_launch(launch.lane, generation="generation-1") is True
    assert await store.claim_lane_launch(launch.lane, generation="generation-1") is False
    await store.close()

    reopened = await Registry.open(db, now=_clock)
    try:
        held = await reopened.get_lane_launch(launch.lane)
        assert held.status == "creating"
        assert await reopened.claim_lane_launch(launch.lane, generation="generation-1") is False
    finally:
        await reopened.close()


async def test_positive_mapping_without_prompt_finishes_creation(store: Registry) -> None:
    _, lane, _ = await _reserve(store)
    assert await store.claim_lane_launch(lane.id, generation="generation-1")

    mapped = await store.record_lane_launch_mapping(
        lane.id,
        generation="generation-1",
        runtime_session_id="runtime-1",
        stored_session_id="stored-1",
        effective_cwd="/effective",
        first_delivery_required=False,
    )

    assert mapped.status == "created"
    assert mapped.runtime_session_id == "runtime-1"
    assert mapped.stored_session_id == "stored-1"
    saved_lane = await store.get_lane(lane.id)
    assert saved_lane.provider_session_id == "stored-1"
    assert saved_lane.cwd == "/effective"
    assert saved_lane.status == "idle"


async def test_positive_mapping_with_prompt_remains_held_until_receipt(store: Registry) -> None:
    _, lane, _ = await _reserve(store)
    assert await store.claim_lane_launch(lane.id, generation="generation-1")
    mapped = await store.record_lane_launch_mapping(
        lane.id,
        generation="generation-1",
        runtime_session_id="runtime-1",
        stored_session_id="stored-1",
        effective_cwd="/effective",
        first_delivery_required=True,
    )

    assert mapped.status == "creating"
    assert mapped.first_delivery_id is None
    assert (await store.get_lane(lane.id)).status == "unknown"


async def test_provider_session_collision_rolls_back_lane_and_launch_mapping(
    store: Registry,
) -> None:
    _, first, _ = await _reserve(store, key="first")
    _, second, _ = await _reserve(store, key="second")
    assert await store.claim_lane_launch(first.id, generation="generation-1")
    assert await store.claim_lane_launch(second.id, generation="generation-1")
    await store.record_lane_launch_mapping(
        first.id,
        generation="generation-1",
        runtime_session_id="runtime-first",
        stored_session_id="stored-shared",
        effective_cwd="/first",
        first_delivery_required=False,
    )

    with pytest.raises(aiosqlite.IntegrityError):
        await store.record_lane_launch_mapping(
            second.id,
            generation="generation-1",
            runtime_session_id="runtime-second",
            stored_session_id="stored-shared",
            effective_cwd="/second",
            first_delivery_required=False,
        )

    assert (await store.get_lane(second.id)).provider_session_id is None
    held = await store.get_lane_launch(second.id)
    assert held.status == "creating"
    assert held.runtime_session_id is None
    assert held.stored_session_id is None


async def test_first_delivery_is_reserved_and_linked_atomically(store: Registry) -> None:
    _, lane, _ = await _reserve(store)
    assert await store.claim_lane_launch(lane.id, generation="generation-1")
    await store.record_lane_launch_mapping(
        lane.id,
        generation="generation-1",
        runtime_session_id="runtime-1",
        stored_session_id="stored-1",
        effective_cwd="/effective",
        first_delivery_required=True,
    )
    prepared = json.dumps(
        {
            "target": {
                "lane_id": lane.id,
                "provider": "hermes",
                "binding_id": "hermes-default",
                "stored_session_id": "stored-1",
                "runtime_session_id": "runtime-1",
                "generation": "generation-1",
            }
        },
        sort_keys=True,
    )

    receipt = await store.reserve_lane_launch_first_delivery(
        lane.id,
        submitted_payload='{"text":"hello"}',
        payload=prepared,
        text="hello",
    )

    launch = await store.get_lane_launch(lane.id)
    assert receipt.key is None
    assert receipt.status == "queued"
    assert receipt.lane == lane.id
    assert receipt.provider == "hermes"
    assert receipt.binding_id == "hermes-default"
    assert receipt.native_session_id == "stored-1"
    assert launch.first_delivery_id == receipt.id
    assert launch.status == "created"
    assert (await store.get_lane(lane.id)).status == "idle"

    await store.update_lane_provider_session(
        lane.id,
        provider="hermes",
        binding_id="hermes-default",
        provider_session_id="stored-after-compression",
    )
    immutable = await store.get_lane_launch(lane.id)
    assert immutable.runtime_session_id == "runtime-1"
    assert immutable.stored_session_id == "stored-1"
    assert (await store.get_delivery(receipt.id)).native_session_id == "stored-1"


async def test_first_delivery_failure_rolls_back_receipt_link_and_state(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lane, _ = await _reserve(store)
    assert await store.claim_lane_launch(lane.id, generation="generation-1")
    await store.record_lane_launch_mapping(
        lane.id,
        generation="generation-1",
        runtime_session_id="runtime-1",
        stored_session_id="stored-1",
        effective_cwd="/effective",
        first_delivery_required=True,
    )

    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected receipt failure")

    monkeypatch.setattr(Registry, "_insert_delivery_reservation", fail)
    with pytest.raises(RuntimeError, match="injected"):
        await store.reserve_lane_launch_first_delivery(
            lane.id,
            submitted_payload='{"text":"hello"}',
            payload="{}",
            text="hello",
        )

    launch = await store.get_lane_launch(lane.id)
    assert launch.status == "creating"
    assert launch.first_delivery_id is None
    async with store._conn.execute(
        "SELECT count(*) FROM deliveries WHERE lane = ?", (lane.id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and int(row[0]) == 0
    assert (await store.get_lane(lane.id)).status == "unknown"


@pytest.mark.parametrize("terminal", ["failed", "ambiguous"])
async def test_terminal_creation_attempt_cannot_be_reclaimed(
    store: Registry, terminal: str
) -> None:
    launch, _, _ = await _reserve(store)
    assert await store.claim_lane_launch(launch.lane, generation="generation-1")
    if terminal == "failed":
        result = await store.fail_lane_launch(
            launch.lane, generation="generation-1", error="rejected"
        )
    else:
        result = await store.mark_lane_launch_ambiguous(
            launch.lane, generation="generation-1", error="response lost"
        )

    assert result.status == terminal
    assert (await store.get_lane(launch.lane)).status == "error"
    assert await store.claim_lane_launch(launch.lane, generation="generation-1") is False
    with pytest.raises(ValidationError, match="cannot record"):
        await store.record_lane_launch_mapping(
            launch.lane,
            generation="generation-1",
            runtime_session_id="runtime-1",
            stored_session_id="stored-1",
            effective_cwd="/work",
            first_delivery_required=False,
        )


async def test_v26_migration_preserves_rows_and_adds_lane_launches(tmp_path: Path) -> None:
    db = tmp_path / "registry-v26.db"
    seeded = await Registry.open(db, now=_clock)
    lane = await seeded.add_lane(
        id="dsp_existing",
        handle="@existing",
        source="own",
        provider="hermes",
        binding_id="hermes-default",
    )
    receipt, _ = await seeded.reserve_delivery(
        key="existing-delivery",
        lane=lane.id,
        mode="send",
        payload="{}",
        text="existing",
        provider="hermes",
        binding_id="hermes-default",
    )
    await seeded.close()

    conn = await aiosqlite.connect(db)
    await conn.execute("DROP TABLE lane_launches")
    await conn.execute("PRAGMA user_version = 26")
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        assert (await migrated.get_lane(lane.id)).id == lane.id
        assert (await migrated.get_delivery(receipt.id)).id == receipt.id
        async with migrated._conn.execute("PRAGMA table_info(lane_launches)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        assert {
            "lane",
            "key",
            "submitted_payload",
            "request_payload",
            "generation",
            "runtime_session_id",
            "stored_session_id",
            "first_delivery_id",
        } <= columns
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None and int(row[0]) == SCHEMA_VERSION == 27
        async with migrated._conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []
    finally:
        await migrated.close()
