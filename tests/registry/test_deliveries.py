"""Durable delivery ledger behavior."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import DeliveryConflictError
from outfitter.dispatch.registry.delivery import DeliveryExecutionStatus
from outfitter.dispatch.registry.store import Registry


def _clock() -> datetime:
    return datetime(2026, 9, 8, 18, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    registry = await Registry.open(now=_clock)
    await registry.add_lane(id="lane-1", handle="@one", source="own")
    try:
        yield registry
    finally:
        await registry.close()


async def test_reserve_direct_delivery_roundtrips_by_id_and_key(store: Registry) -> None:
    receipt, created = await store.reserve_delivery(
        delivery_id="11111111-1111-4111-8111-111111111111",
        key="buzz:event-1",
        lane="lane-1",
        mode="send",
        payload='{"settings":{},"text":"hello"}',
        text="hello",
    )

    assert created is True
    assert receipt.id == "11111111-1111-4111-8111-111111111111"
    assert receipt.status == "queued"
    assert receipt.queue_id is None
    assert await store.get_delivery(receipt.id) == receipt
    assert await store.get_delivery_by_key("buzz:event-1") == receipt


async def test_key_is_idempotent_but_conflicting_reuse_fails(store: Registry) -> None:
    first, created = await store.reserve_delivery(
        key="buzz:event-2",
        lane="lane-1",
        mode="send",
        payload='{"text":"same"}',
        text="same",
    )
    repeated, repeated_created = await store.reserve_delivery(
        key="buzz:event-2",
        lane="lane-1",
        mode="send",
        payload='{"text":"same"}',
        text="ignored on replay",
    )

    assert created is True
    assert repeated_created is False
    assert repeated == first
    with pytest.raises(DeliveryConflictError, match="already bound"):
        await store.reserve_delivery(
            key="buzz:event-2",
            lane="lane-1",
            mode="send",
            payload='{"text":"different"}',
            text="different",
        )


async def test_queue_reservation_atomically_links_legacy_queue(store: Registry) -> None:
    receipt, created = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload='{"text":"[delivery:d1] hello"}',
        text="[delivery:d1] hello",
    )

    assert created is True
    assert receipt.queue_id is not None
    assert await store.delivery_for_queue(receipt.queue_id) == receipt
    queued = await store.get_queued_message(receipt.queue_id)
    assert queued.text == "[delivery:d1] hello"
    assert queued.status == "pending"


async def test_claim_holds_lane_and_claims_queue_atomically(store: Registry) -> None:
    first, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload='{"text":"one"}',
        text="one",
    )
    second, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload='{"text":"two"}',
        text="two",
    )

    assert await store.claim_delivery(second.id) is False
    assert await store.claim_delivery(first.id) is True
    assert await store.claim_delivery(first.id) is False
    assert await store.claim_delivery(second.id) is False
    assert await store.lane_delivery_held("lane-1") is True
    assert first.queue_id is not None and second.queue_id is not None
    assert (await store.get_queued_message(first.queue_id)).status == "sending"
    assert (await store.get_queued_message(second.queue_id)).status == "pending"

    accepted = await store.update_delivery(first.id, status="accepted", turn_id="turn-1")
    assert accepted.status == "accepted"
    assert accepted.turn_id == "turn-1"
    assert (await store.get_queued_message(first.queue_id)).status == "sent"
    assert await store.claim_delivery(second.id) is True


async def test_terminal_updates_preserve_turn_and_drive_queue_status(store: Registry) -> None:
    receipt, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload='{"text":"one"}',
        text="one",
    )
    assert await store.claim_delivery(receipt.id)
    completed = await store.update_delivery(receipt.id, status="completed", turn_id="turn-1")
    unchanged = await store.update_delivery(receipt.id, status="accepted")
    assert unchanged.status == "completed"
    assert unchanged.turn_id == "turn-1"
    assert await store.delivery_for_turn("lane-1", "turn-1") == [completed]

    failed, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload='{"text":"bad"}',
        text="bad",
    )
    await store.update_delivery(failed.id, status="failed", error="rejected")
    assert failed.queue_id is not None
    queued = await store.get_queued_message(failed.queue_id)
    assert queued.status == "error"
    assert queued.error == "rejected"


@pytest.mark.parametrize("terminal", ["failed", "interrupted"])
async def test_execution_failure_remains_provider_accepted(
    store: Registry,
    terminal: DeliveryExecutionStatus,
) -> None:
    receipt, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="send",
        payload='{"text":"run"}',
        text="run",
    )
    await store.update_delivery(receipt.id, status="accepted", turn_id="turn-2")
    observed = await store.update_delivery(
        receipt.id,
        status="accepted",
        execution_status=terminal,
        error="model execution failed",
    )

    assert observed.status == "accepted"
    assert observed.execution_status == terminal
    assert observed.turn_id == "turn-2"
    stale = await store.update_delivery(
        receipt.id,
        status="accepted",
        execution_status="inProgress",
        error=None,
    )
    assert stale == observed


@pytest.mark.parametrize("accepted_status", ["accepted", "completed"])
async def test_late_rejection_cannot_undo_provider_acceptance(
    store: Registry,
    accepted_status: str,
) -> None:
    receipt, _ = await store.reserve_delivery(
        key="late-rejection",
        lane="lane-1",
        mode="queue",
        payload='{"text":"run"}',
        text="run",
    )
    accepted = await store.update_delivery(
        receipt.id,
        status="completed" if accepted_status == "completed" else "accepted",
        turn_id="turn-1",
    )
    stale = await store.update_delivery(receipt.id, status="failed", error="late rejection")
    assert stale == accepted
    assert receipt.queue_id is not None
    assert (await store.get_queued_message(receipt.queue_id)).status == "sent"


async def test_recovery_marks_only_submitting_ambiguous_and_preserves_queue(
    store: Registry,
) -> None:
    active, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload='{"text":"active"}',
        text="active",
    )
    waiting, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload='{"text":"waiting"}',
        text="waiting",
    )
    assert await store.claim_delivery(active.id)

    assert await store.recover_deliveries() == 1
    unresolved = await store.list_unresolved_deliveries()
    assert [item.id for item in unresolved] == [active.id]
    assert unresolved[0].status == "ambiguous"
    assert [item.id for item in await store.list_waiting_deliveries()] == [waiting.id]
    assert await store.reset_sending_messages() == 0
    assert active.queue_id is not None
    assert (await store.get_queued_message(active.queue_id)).status == "sending"

    checked = await store.note_delivery_check(active.id, "no provider evidence")
    assert checked is not None
    assert checked.reconciliation_attempts == 1
    assert checked.error == "no provider evidence"

    legacy = await store.enqueue_message(lane="lane-1", text="legacy later work")
    assert await store.claim_queued_message(legacy.id) is False
    assert (await store.get_queued_message(legacy.id)).status == "pending"


async def test_direct_acceptance_is_not_blocked_by_uncertain_queue_work(
    store: Registry,
) -> None:
    uncertain, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload='{"text":"uncertain"}',
        text="uncertain",
    )
    assert await store.claim_delivery(uncertain.id)
    await store.recover_deliveries()
    direct, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="send",
        payload='{"text":"already accepted"}',
        text="already accepted",
    )

    accepted = await store.update_delivery(direct.id, status="accepted", turn_id="turn-direct")
    assert accepted.status == "accepted"
    assert accepted.turn_id == "turn-direct"


async def test_v21_migration_adds_delivery_ledger(tmp_path: Path) -> None:
    db = tmp_path / "registry-v21.db"
    seeded = await Registry.open(db, now=_clock)
    await seeded.add_lane(id="lane-1", handle="@one", source="own")
    await seeded.close()

    conn = await aiosqlite.connect(db)
    await conn.execute("DROP TABLE deliveries")
    await conn.execute("PRAGMA user_version = 21")
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        receipt, created = await migrated.reserve_delivery(
            key="after:migration",
            lane="lane-1",
            mode="send",
            payload='{"text":"works"}',
            text="works",
        )
        assert created is True
        assert receipt.key == "after:migration"
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None and int(row[0]) == 22
    finally:
        await migrated.close()


async def test_two_connections_reserve_key_and_claim_lane_once(tmp_path: Path) -> None:
    db = tmp_path / "deliveries.db"
    first = await Registry.open(db, now=_clock)
    await first.add_lane(id="lane-1", handle="@one", source="own")
    second = await Registry.open(db, now=_clock)
    try:
        reservations = await asyncio.gather(
            first.reserve_delivery(
                key="buzz:shared",
                lane="lane-1",
                mode="send",
                payload='{"text":"same"}',
                text="same",
            ),
            second.reserve_delivery(
                key="buzz:shared",
                lane="lane-1",
                mode="send",
                payload='{"text":"same"}',
                text="same",
            ),
        )
        assert sorted(created for _, created in reservations) == [False, True]
        assert len({receipt.id for receipt, _ in reservations}) == 1

        one, _ = await first.reserve_delivery(
            key=None, lane="lane-1", mode="send", payload='{"text":"one"}', text="one"
        )
        two, _ = await second.reserve_delivery(
            key=None, lane="lane-1", mode="send", payload='{"text":"two"}', text="two"
        )
        claims = await asyncio.gather(first.claim_delivery(one.id), second.claim_delivery(two.id))
        assert sorted(claims) == [False, True]
    finally:
        await first.close()
        await second.close()


async def test_reconcile_idle_uses_lane_update_compare_and_swap() -> None:
    current = _clock()

    def clock() -> datetime:
        return current

    registry = await Registry.open(now=clock)
    try:
        await registry.add_lane(id="lane-1", handle="@one", source="own", status="busy")
        stale = await registry.get_lane("lane-1")
        await registry.record_turn_started("lane-1", "newer-turn")

        assert (
            await registry.reconcile_lane_idle(
                "lane-1",
                stale.updated_at,
                expected_status=stale.status,
                expected_active_turn_id=stale.active_turn_id,
            )
            is False
        )
        still_busy = await registry.get_lane("lane-1")
        assert still_busy.status == "busy"
        assert still_busy.active_turn_id == "newer-turn"

        current += timedelta(seconds=1)
        assert (
            await registry.reconcile_lane_idle(
                "lane-1",
                still_busy.updated_at,
                expected_status=still_busy.status,
                expected_active_turn_id=still_busy.active_turn_id,
            )
            is True
        )
        idle = await registry.get_lane("lane-1")
        assert idle.status == "idle"
        assert idle.active_turn_id is None
    finally:
        await registry.close()


async def test_reconcile_idle_refuses_active_delivery_or_sending_queue(
    store: Registry,
) -> None:
    await store.update_lane_status("lane-1", "busy")
    snapshot = await store.get_lane("lane-1")
    delivery, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="send",
        payload='{"text":"new submission"}',
        text="new submission",
    )
    assert await store.claim_delivery(delivery.id)
    assert (
        await store.reconcile_lane_idle(
            "lane-1",
            snapshot.updated_at,
            expected_status=snapshot.status,
            expected_active_turn_id=snapshot.active_turn_id,
        )
        is False
    )

    await store.update_delivery(delivery.id, status="accepted", turn_id="turn-new")
    queued = await store.enqueue_message(lane="lane-1", text="legacy sending")
    assert await store.claim_queued_message(queued.id)
    assert (
        await store.reconcile_lane_idle(
            "lane-1",
            snapshot.updated_at,
            expected_status=snapshot.status,
            expected_active_turn_id=snapshot.active_turn_id,
        )
        is False
    )

    await store.complete_queued_message(queued.id)
    assert (
        await store.reconcile_lane_idle(
            "lane-1",
            snapshot.updated_at,
            expected_status=snapshot.status,
            expected_active_turn_id=snapshot.active_turn_id,
        )
        is True
    )


async def test_stale_negative_update_cannot_overwrite_provider_acceptance(
    store: Registry,
) -> None:
    receipt, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="send",
        payload='{"text":"race"}',
        text="race",
    )
    completed = await store.update_delivery(
        receipt.id,
        status="completed",
        turn_id="turn-race",
        execution_status="completed",
        error="completed detail",
    )

    after_negative = await store.update_delivery(
        receipt.id, status="ambiguous", error="late timeout"
    )
    after_acceptance = await store.update_delivery(
        receipt.id,
        status="accepted",
        execution_status="inProgress",
        error=None,
    )

    assert after_negative == completed
    assert after_acceptance == completed


async def test_delivery_check_budget_is_atomic_across_connections(tmp_path: Path) -> None:
    db = tmp_path / "reconcile-budget.db"
    first = await Registry.open(db, now=_clock)
    await first.add_lane(id="lane-1", handle="@one", source="own")
    receipt, _ = await first.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="send",
        payload='{"text":"uncertain"}',
        text="uncertain",
    )
    await first.update_delivery(receipt.id, status="ambiguous", error="unknown")
    second = await Registry.open(db, now=_clock)
    try:
        results = await asyncio.gather(
            *(
                store.note_delivery_check(receipt.id, "checking", max_attempts=3)
                for store in (
                    first,
                    second,
                    first,
                    second,
                    first,
                    second,
                )
            )
        )
        assert sum(result is not None for result in results) == 3
        final = await first.get_delivery(receipt.id)
        assert final.reconciliation_attempts == 3

        await first.update_delivery(receipt.id, status="accepted", turn_id="turn-found")
        assert await second.note_delivery_check(receipt.id, "too late", max_attempts=None) is None
    finally:
        await first.close()
        await second.close()
