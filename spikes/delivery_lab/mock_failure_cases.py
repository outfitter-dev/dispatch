"""Restart and ambiguous-outcome cases for the PAT-176 mock lab."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from spikes.delivery_lab.mock_support import (
    DESTINATION_ID,
    AcceptedThenAckLostClient,
    AcceptedThenCrashClient,
    SyntheticProcessCrash,
    base_result,
    receipt_evidence,
)
from tests.fakes import make_ctx

from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.core import handlers, queue
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.registry.store import Registry


async def accepted_then_ack_lost_restart_retry(root: Path) -> dict[str, object]:
    started = perf_counter()
    registry_path = root / "ambiguous.sqlite3"
    event_id = "buzz-event-ambiguous-001"
    envelope = f"[mock Buzz event id={event_id}] ambiguous delivery"
    provider_accepts: list[dict[str, str]] = []

    first_store = await Registry.open(registry_path)
    try:
        await first_store.add_lane(
            id=DESTINATION_ID, handle="@mock-delivery", source="own", status="idle"
        )
        first_client = AcceptedThenAckLostClient(provider_accepts, lose_ack=True)
        first_ctx = make_ctx(first_store, first_client)
        first_error: str | None = None
        try:
            await handlers.send_message(
                SendInput(lane=DESTINATION_ID, text=envelope, mode="send"), first_ctx
            )
        except TransportError as exc:
            first_error = str(exc)
        first_receipts = await first_store.list_message_receipts(lane=DESTINATION_ID)
    finally:
        await first_store.close()

    reopened_store = await Registry.open(registry_path)
    try:
        second_client = AcceptedThenAckLostClient(provider_accepts, lose_ack=False)
        second_ctx = make_ctx(reopened_store, second_client)
        await handlers.send_message(
            SendInput(lane=DESTINATION_ID, text=envelope, mode="send"), second_ctx
        )
        final_receipts = await reopened_store.list_message_receipts(lane=DESTINATION_ID)
        observed_turn_count = len(provider_accepts)
        passed = (
            first_error == "synthetic ACK lost after provider acceptance"
            and len(first_receipts) == 1
            and first_receipts[0].status == "failed"
            and observed_turn_count == 2
            and sorted(receipt.status for receipt in final_receipts) == ["failed", "sent"]
            and all(receipt.dispatch_message_id is None for receipt in final_receipts)
        )
        result = base_result(
            scenario="accepted_then_ack_lost_restart_retry",
            event_ids=[event_id, event_id],
            expected_turn_count=2,
            observed_turn_count=observed_turn_count,
            elapsed_ms=(perf_counter() - started) * 1000,
            passed=passed,
        )
        result.update(
            {
                "verdict": "ambiguous_outcome_retry_limitation_reproduced",
                "first_attempt": {
                    "provider_accepted": True,
                    "dispatch_error": first_error,
                    "receipts": receipt_evidence(first_receipts),
                },
                "restart_evidence": {
                    "registry_reopened": True,
                    "same_registry_path": True,
                    "source_event_identity_persisted_as_key": False,
                },
                "provider_accepts": provider_accepts,
                "receipts": receipt_evidence(final_receipts),
                "limitation": (
                    "A failed local receipt cannot distinguish provider rejection from ACK "
                    "loss, and the public send input/receipt path has no external event key "
                    "for safe retry deduplication."
                ),
            }
        )
        return result
    finally:
        await reopened_store.close()


async def queued_accept_then_crash_restart_drain(root: Path) -> dict[str, object]:
    started = perf_counter()
    registry_path = root / "queue-crash.sqlite3"
    event_id = "buzz-event-queue-crash-001"
    envelope = f"[mock Buzz event id={event_id}] queued crash-window delivery"
    provider_accepts: list[dict[str, str]] = []

    first_store = await Registry.open(registry_path)
    try:
        await first_store.add_lane(
            id=DESTINATION_ID, handle="@mock-delivery", source="own", status="busy"
        )
        first_ctx = make_ctx(first_store, AcceptedThenCrashClient(provider_accepts))
        await handlers.send_message(
            SendInput(lane=DESTINATION_ID, text=envelope, mode="queue"), first_ctx
        )
        await first_store.mark_lane_idle(DESTINATION_ID)
        crash_error: str | None = None
        try:
            await queue.drain_next_queued_message(first_ctx, DESTINATION_ID)
        except SyntheticProcessCrash as exc:
            crash_error = str(exc)
        before_restart = await first_store.get_queued_message(1)
        receipts_before_restart = await first_store.list_message_receipts(lane=DESTINATION_ID)
    finally:
        await first_store.close()

    reopened_store = await Registry.open(registry_path)
    try:
        # A real provider/thread reconciliation would decide whether the lane is idle.
        # This lab deliberately assumes that recovery has established idle before the
        # daemon's startup queue drain is allowed to retry pending work.
        await reopened_store.mark_lane_idle(DESTINATION_ID)
        second_client = AcceptedThenAckLostClient(provider_accepts, lose_ack=False)
        second_ctx = make_ctx(reopened_store, second_client)
        drained_count = await queue.drain_idle_queues(second_ctx)
        after_restart = await reopened_store.get_queued_message(1)
        final_receipts = await reopened_store.list_message_receipts(lane=DESTINATION_ID)
        observed_turn_count = len(provider_accepts)
        passed = (
            crash_error == "synthetic crash after provider acceptance"
            and before_restart.status == "sending"
            and len(receipts_before_restart) == 1
            and receipts_before_restart[0].status == "created"
            and drained_count == 1
            and after_restart.status == "sent"
            and observed_turn_count == 2
            and len(final_receipts) == 1
            and final_receipts[0].status == "sent"
            and final_receipts[0].dispatch_message_id == "queue:1"
        )
        result = base_result(
            scenario="queued_accept_then_crash_restart_drain",
            event_ids=[event_id, event_id],
            expected_turn_count=2,
            observed_turn_count=observed_turn_count,
            elapsed_ms=(perf_counter() - started) * 1000,
            passed=passed,
        )
        result.update(
            {
                "verdict": "queue_crash_window_duplicate_limitation_reproduced",
                "artificial_seam": (
                    "AcceptedThenCrashClient raises an uncaught SyntheticProcessCrash "
                    "immediately after FakeLaneClient.turn_start records provider acceptance. "
                    "The exception occurs before drain_next_queued_message records a sent "
                    "receipt or completes the queue row."
                ),
                "assumptions": [
                    "The fake turn_start call represents provider acceptance.",
                    "The process dies before any later queue bookkeeping executes.",
                    "After Registry reopen, provider/thread reconciliation establishes that "
                    "the destination lane is idle.",
                    "drain_idle_queues represents startup recovery: it resets all sending "
                    "rows to pending before draining idle lanes.",
                ],
                "before_restart": {
                    "queue": before_restart.model_dump(mode="json"),
                    "receipts": receipt_evidence(receipts_before_restart),
                    "crash_error": crash_error,
                    "provider_accepts": 1,
                },
                "restart_evidence": {
                    "registry_reopened": True,
                    "lane_marked_idle_after_assumed_reconciliation": True,
                    "startup_drained_count": drained_count,
                    "queue_after_recovery": after_restart.model_dump(mode="json"),
                },
                "provider_accepts": provider_accepts,
                "receipts": receipt_evidence(final_receipts),
                "limitation": (
                    "The final queue:1 receipt is sent and does not retain evidence of the "
                    "first accepted provider turn. Resetting sending to pending therefore "
                    "provides at-least-once recovery with a duplicate window."
                ),
            }
        )
        return result
    finally:
        await reopened_store.close()
