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
from outfitter.dispatch.registry.delivery import DeliveryExecutionStatus, DeliveryReceipt
from outfitter.dispatch.registry.observations import ProviderCorrelation, ProviderObservation
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


async def test_native_claim_preserves_fifo_after_ambiguous_predecessor(store: Registry) -> None:
    async def reserve(text: str) -> DeliveryReceipt:
        receipt, _ = await store.reserve_delivery(
            key=None,
            lane="lane-1",
            mode="queue",
            payload=text,
            text=text,
            transport="native_queue",
        )
        return receipt

    first = await reserve("one")
    assert await store.claim_delivery(first.id)
    await store.update_delivery(first.id, status="ambiguous")
    second = await reserve("two")
    assert not await store.claim_delivery(second.id)
    await store.update_delivery(first.id, status="accepted", submission_id="native-one")
    third = await reserve("three")
    assert not await store.claim_delivery(third.id)
    assert await store.claim_delivery(second.id)
    await store.update_delivery(second.id, status="accepted", submission_id="native-two")
    assert await store.claim_delivery(third.id)


@pytest.mark.parametrize("status", ["pending", "sending"])
async def test_native_claim_holds_legacy_queue_after_upgrade(store: Registry, status: str) -> None:
    legacy = await store.enqueue_message(lane="lane-1", text="older legacy request")
    if status == "sending":
        assert await store.claim_queued_message(legacy.id)
    native, _ = await store.reserve_delivery(
        key=None,
        lane="lane-1",
        mode="queue",
        payload="new",
        text="new",
        transport="native_queue",
    )
    assert not await store.claim_delivery(native.id)
    await store.complete_queued_message(legacy.id)
    assert await store.claim_delivery(native.id)


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


async def test_receipt_observation_requires_exact_frozen_correlation(store: Registry) -> None:
    receipt, _ = await store.reserve_delivery(
        delivery_id="receipt-1",
        key=None,
        lane="lane-1",
        mode="send",
        payload='{"text":"one"}',
        text="one",
        provider="codex",
        binding_id="codex-default",
        native_session_id="lane-1",
        correlation_id="receipt-1",
    )
    base = ProviderObservation(
        provider="codex",
        binding_id="codex-default",
        native_session_id="lane-1",
        kind="accepted",
        correlation=ProviderCorrelation(
            delivery_id=receipt.id,
            correlation_id=receipt.id,
            native_run_id="turn-1",
        ),
        generation="generation-1",
        source="submit_result",
        received_at=_clock(),
    )

    missing = await store.apply_receipt_observation(
        base.model_copy(update={"correlation": ProviderCorrelation(delivery_id=receipt.id)})
    )
    mismatched = await store.apply_receipt_observation(
        base.model_copy(update={"binding_id": "other"})
    )
    missing_terminal = await store.apply_receipt_observation(
        base.model_copy(
            update={
                "kind": "completed",
                "correlation": base.correlation.model_copy(update={"native_run_id": None}),
            }
        )
    )
    missing_acceptance = await store.apply_receipt_observation(
        base.model_copy(
            update={
                "correlation": base.correlation.model_copy(update={"native_run_id": None}),
            }
        )
    )
    accepted = await store.apply_receipt_observation(base)
    conflicting_run = await store.apply_receipt_observation(
        base.model_copy(
            update={
                "kind": "completed",
                "correlation": base.correlation.model_copy(update={"native_run_id": "turn-2"}),
                "source": "history",
            }
        )
    )

    assert not missing.matched and missing.reason == "missing receipt correlation"
    assert not mismatched.matched and mismatched.reason == "provider binding mismatch"
    assert not missing_terminal.matched and missing_terminal.reason == "missing native run evidence"
    assert not missing_acceptance.matched
    assert missing_acceptance.reason == "missing positive provider evidence"
    assert accepted.matched and accepted.changed
    assert accepted.receipt is not None
    assert accepted.receipt.status == "accepted"
    assert accepted.receipt.turn_id == "turn-1"
    assert accepted.receipt.evidence_source == "submit_result"
    assert not conflicting_run.matched and conflicting_run.reason == "native run mismatch"
    assert (await store.get_delivery(receipt.id)).status == "accepted"


async def test_terminal_receipt_state_and_provenance_are_absorbing(store: Registry) -> None:
    receipt, _ = await store.reserve_delivery(
        delivery_id="receipt-terminal",
        key=None,
        lane="lane-1",
        mode="send",
        payload='{"text":"one"}',
        text="one",
    )

    def observation(kind: str, source: str, *, partial: bool) -> ProviderObservation:
        return ProviderObservation.model_validate(
            {
                "provider": "codex",
                "binding_id": "codex-default",
                "native_session_id": "lane-1",
                "kind": kind,
                "correlation": {
                    "delivery_id": receipt.id,
                    "correlation_id": receipt.id,
                    "native_run_id": "turn-1",
                },
                "source": source,
                "received_at": _clock(),
                "partial": partial,
                "reason": "late failure" if kind == "failed" else None,
            }
        )

    completed = await store.apply_receipt_observation(
        observation("completed", "live", partial=False)
    )
    late_failed = await store.apply_receipt_observation(
        observation("failed", "history", partial=True)
    )
    stale_started = await store.apply_receipt_observation(
        observation("started", "history", partial=True)
    )

    assert completed.receipt is not None
    assert completed.receipt.status == "completed"
    assert completed.receipt.execution_status == "completed"
    assert completed.receipt.evidence_source == "live"
    assert completed.receipt.evidence_partial is False
    assert late_failed.matched and not late_failed.changed
    assert stale_started.matched and not stale_started.changed
    assert late_failed.receipt == completed.receipt
    assert stale_started.receipt == completed.receipt

    legacy, _ = await store.reserve_delivery(
        delivery_id="receipt-legacy-completed",
        key=None,
        lane="lane-1",
        mode="send",
        payload='{"text":"legacy"}',
        text="legacy",
    )
    legacy_completed = await store.update_delivery(legacy.id, status="completed")
    legacy_failed = await store.apply_receipt_observation(
        ProviderObservation.model_validate(
            {
                "provider": "codex",
                "binding_id": "codex-default",
                "native_session_id": "lane-1",
                "kind": "failed",
                "correlation": {
                    "delivery_id": legacy.id,
                    "correlation_id": legacy.id,
                    "native_run_id": "turn-legacy",
                },
                "source": "history",
                "received_at": _clock(),
                "partial": True,
                "reason": "late failure",
            }
        )
    )

    assert legacy_completed.execution_status is None
    assert legacy_failed.matched and not legacy_failed.changed
    assert legacy_failed.receipt == legacy_completed


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
        assert row is not None and int(row[0]) == 26
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


async def test_concurrent_duplicate_uses_submitted_intent_and_keeps_first_prepared_request(
    tmp_path: Path,
) -> None:
    db = tmp_path / "submitted-intent.db"
    first = await Registry.open(db, now=_clock)
    await first.add_lane(id="lane-1", handle="@one", source="own")
    await first.add_lane(id="lane-2", handle="@two", source="own")
    second = await Registry.open(db, now=_clock)
    submitted = '{"lane":"@moving","mode":"send","text":"same","version":1}'
    try:
        reservations = await asyncio.gather(
            first.reserve_delivery(
                key="shared",
                lane="lane-1",
                mode="send",
                submitted_payload=submitted,
                payload='{"prepared":"first"}',
                text="first",
            ),
            second.reserve_delivery(
                key="shared",
                lane="lane-2",
                mode="send",
                submitted_payload=submitted,
                payload='{"prepared":"second"}',
                text="second",
            ),
        )

        assert sorted(created for _, created in reservations) == [False, True]
        receipts = [receipt for receipt, _ in reservations]
        assert receipts[0] == receipts[1]
        assert (receipts[0].lane, receipts[0].payload) in {
            ("lane-1", '{"prepared":"first"}'),
            ("lane-2", '{"prepared":"second"}'),
        }
        with pytest.raises(DeliveryConflictError, match="already bound"):
            await first.reserve_delivery(
                key="shared",
                lane=receipts[0].lane,
                mode="send",
                submitted_payload='{"lane":"@moving","text":"changed","version":1}',
                payload=receipts[0].payload,
                text="changed",
            )
    finally:
        await first.close()
        await second.close()


async def test_v24_migration_adds_nullable_submitted_intent(tmp_path: Path) -> None:
    db = tmp_path / "registry-v24.db"
    seeded = await Registry.open(db, now=_clock)
    await seeded.close()

    conn = await aiosqlite.connect(db)
    await conn.execute("ALTER TABLE deliveries DROP COLUMN submitted_payload")
    await conn.execute("PRAGMA user_version = 24")
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        async with migrated._conn.execute("PRAGMA table_info(deliveries)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        assert "submitted_payload" in columns
        async with migrated._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None and int(row[0]) == 26
    finally:
        await migrated.close()


async def test_v25_migration_adds_receipt_observation_identity_and_evidence(tmp_path: Path) -> None:
    db = tmp_path / "registry-v25.db"
    seeded = await Registry.open(db, now=_clock)
    await seeded.add_lane(id="lane-1", handle="@one", source="own")
    receipt, _ = await seeded.reserve_delivery(
        delivery_id="legacy-receipt",
        key=None,
        lane="lane-1",
        mode="send",
        payload=(
            '{"request":{"correlation_id":"legacy-receipt","target":'
            '{"binding_id":"local","native_session_id":"conversation-1",'
            '"provider":"hermes"}},"text":"legacy","version":1}'
        ),
        text="legacy",
    )
    await seeded.close()

    conn = await aiosqlite.connect(db)
    await conn.execute("DROP INDEX idx_deliveries_provider_run")
    for column in (
        "provider",
        "binding_id",
        "native_session_id",
        "correlation_id",
        "evidence_source",
        "evidence_provider_time",
        "evidence_received_at",
        "evidence_partial",
        "evidence_generation",
    ):
        await conn.execute(f"ALTER TABLE deliveries DROP COLUMN {column}")
    await conn.execute("PRAGMA user_version = 25")
    await conn.commit()
    await conn.close()

    migrated = await Registry.open(db, now=_clock)
    try:
        restored = await migrated.get_delivery(receipt.id)
        assert restored.provider == "hermes"
        assert restored.binding_id == "local"
        assert restored.native_session_id == "conversation-1"
        assert restored.correlation_id == receipt.id
        assert restored.evidence_source is None
        assert restored.evidence_partial is False
    finally:
        await migrated.close()


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


async def test_v22_migration_preserves_existing_delivery_and_adds_native_correlation(
    tmp_path: Path,
) -> None:
    db = tmp_path / "registry-v22.db"
    store = await Registry.open(db, now=_clock)
    await store.add_lane(id="lane-1", handle="@one", source="own")
    receipt, _ = await store.reserve_delivery(
        key="old", lane="lane-1", mode="send", payload='{"text":"old"}', text="old"
    )
    await store.update_delivery(receipt.id, status="accepted", turn_id="turn-old")
    await store.close()
    conn = await aiosqlite.connect(db)
    await conn.execute("ALTER TABLE deliveries DROP COLUMN transport")
    await conn.execute("ALTER TABLE deliveries DROP COLUMN submission_id")
    await conn.execute("PRAGMA user_version = 22")
    await conn.commit()
    await conn.close()
    store = await Registry.open(db, now=_clock)
    try:
        old = await store.get_delivery(receipt.id)
        assert old.transport == "turn" and old.turn_id == "turn-old"
        assert old.status == "accepted" and old.submission_id is None
        native, _ = await store.reserve_delivery(
            key="new",
            lane="lane-1",
            mode="queue",
            payload='{"text":"new"}',
            text="new",
            transport="native_queue",
        )
        await store.update_delivery(native.id, status="accepted", submission_id="native-id")
        assert (await store.get_delivery(native.id)).submission_id == "native-id"
        assert native.queue_id is None
    finally:
        await store.close()
