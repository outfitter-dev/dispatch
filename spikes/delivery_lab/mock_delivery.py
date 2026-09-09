"""Mock delivery and failure-injection lab for PAT-176.

This script deliberately uses Dispatch's real handlers, Registry, queue, and
shared FakeLaneClient while avoiding every live Codex or daemon surface. Its
results describe handler/registry behavior only; they are not provider proof.

Run from the repository root with::

    uv run python -m spikes.delivery_lab.mock_delivery
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from time import perf_counter

from spikes.delivery_lab.mock_failure_cases import (
    accepted_then_ack_lost_restart_retry,
    queued_accept_then_crash_restart_drain,
)
from spikes.delivery_lab.mock_support import (
    DESTINATION_ID,
    EXPECTED_OUTCOMES,
    base_result,
    calls,
    receipt_evidence,
    turn_starts,
)
from tests.fakes import FakeLaneClient, make_ctx

from outfitter.dispatch.core import handlers, queue
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.registry.store import Registry


async def duplicate_external_event() -> dict[str, object]:
    started = perf_counter()
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        await store.add_lane(
            id=DESTINATION_ID, handle="@mock-delivery", source="own", status="idle"
        )
        event_id = "buzz-event-duplicate-001"
        envelope = f"[mock Buzz event id={event_id}] synthetic delivery"
        await handlers.send_message(SendInput(lane=DESTINATION_ID, text=envelope, mode="send"), ctx)
        await handlers.send_message(SendInput(lane=DESTINATION_ID, text=envelope, mode="send"), ctx)
        turns = turn_starts(client)
        receipts = await store.list_message_receipts(lane=DESTINATION_ID)
        result = base_result(
            scenario="duplicate_external_event",
            event_ids=[event_id, event_id],
            expected_turn_count=2,
            observed_turn_count=len(turns),
            elapsed_ms=(perf_counter() - started) * 1000,
            passed=len(turns) == 2 and len(receipts) == 2,
        )
        result.update(
            {
                "verdict": "limitation_reproduced",
                "provider_calls": turns,
                "receipts": receipt_evidence(receipts),
                "dedupe_evidence": (
                    "The repeated source event id exists only inside message text; both "
                    "receipts have dispatch_message_id=null and turn_id=null."
                ),
            }
        )
        return result
    finally:
        await store.close()


async def steer_active_turn() -> dict[str, object]:
    started = perf_counter()
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        await store.add_lane(
            id=DESTINATION_ID, handle="@mock-delivery", source="own", status="busy"
        )
        await store.set_active_turn(DESTINATION_ID, "mock-active-turn-001")
        event_id = "buzz-event-steer-001"
        await handlers.send_message(
            SendInput(
                lane=DESTINATION_ID,
                text=f"[mock Buzz event id={event_id}] steer detail",
                mode="steer",
            ),
            ctx,
        )
        turns = turn_starts(client)
        steer_calls = calls(client, "turn_steer")
        result = base_result(
            scenario="steer_active_turn",
            event_ids=[event_id],
            expected_turn_count=0,
            observed_turn_count=len(turns),
            elapsed_ms=(perf_counter() - started) * 1000,
            passed=(
                len(turns) == 0
                and len(steer_calls) == 1
                and steer_calls[0]["expected_turn_id"] == "mock-active-turn-001"
            ),
        )
        result.update(
            {
                "provider_calls": {"turn_start": turns, "turn_steer": steer_calls},
                "receipts": [],
                "behavior_observation": (
                    "Dispatch routed to turn_steer for the active turn. A mock cannot prove "
                    "how a model changes its active behavior."
                ),
            }
        )
        return result
    finally:
        await store.close()


async def inject_context() -> dict[str, object]:
    started = perf_counter()
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        await store.add_lane(
            id=DESTINATION_ID, handle="@mock-delivery", source="own", status="busy"
        )
        await store.set_active_turn(DESTINATION_ID, "mock-active-turn-002")
        event_id = "buzz-event-context-001"
        await handlers.send_message(
            SendInput(
                lane=DESTINATION_ID,
                text=f"[mock Buzz event id={event_id}] additional context",
                mode="context",
            ),
            ctx,
        )
        turns = turn_starts(client)
        injections = calls(client, "inject_items")
        result = base_result(
            scenario="inject_context",
            event_ids=[event_id],
            expected_turn_count=0,
            observed_turn_count=len(turns),
            elapsed_ms=(perf_counter() - started) * 1000,
            passed=len(turns) == 0 and len(injections) == 1,
        )
        result.update(
            {
                "provider_calls": {"turn_start": turns, "inject_items": injections},
                "receipts": [],
                "behavior_observation": (
                    "Dispatch routed to inject_items. A mock cannot prove when or how the "
                    "active model consumes the injected item."
                ),
            }
        )
        return result
    finally:
        await store.close()


async def queued_busy_to_idle() -> dict[str, object]:
    started = perf_counter()
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        await store.add_lane(
            id=DESTINATION_ID, handle="@mock-delivery", source="own", status="busy"
        )
        event_id = "buzz-event-queued-001"
        ack = await handlers.send_message(
            SendInput(
                lane=DESTINATION_ID,
                text=f"[mock Buzz event id={event_id}] deliver after busy turn",
                mode="queue",
            ),
            ctx,
        )
        turns_while_busy = len(turn_starts(client))
        queued_before = await store.get_queued_message(1)
        receipt_before = await store.list_message_receipts(lane=DESTINATION_ID)

        await store.mark_lane_idle(DESTINATION_ID)
        drained = await queue.drain_next_queued_message(ctx, DESTINATION_ID)
        turns = turn_starts(client)
        queued_after = await store.get_queued_message(1)
        receipts_after = await store.list_message_receipts(lane=DESTINATION_ID)
        passed = (
            ack.op == "queue"
            and turns_while_busy == 0
            and queued_before.status == "pending"
            and receipt_before[0].status == "created"
            and drained
            and len(turns) == 1
            and queued_after.status == "sent"
            and receipts_after[0].status == "sent"
        )
        result = base_result(
            scenario="queued_busy_to_idle",
            event_ids=[event_id],
            expected_turn_count=1,
            observed_turn_count=len(turns),
            elapsed_ms=(perf_counter() - started) * 1000,
            passed=passed,
        )
        result.update(
            {
                "provider_calls": turns,
                "queue_evidence": {
                    "turn_count_while_busy": turns_while_busy,
                    "before_idle": queued_before.model_dump(mode="json"),
                    "idle_drain_claimed": drained,
                    "after_idle": queued_after.model_dump(mode="json"),
                },
                "receipts_before_idle": receipt_evidence(receipt_before),
                "receipts": receipt_evidence(receipts_after),
            }
        )
        return result
    finally:
        await store.close()


async def run_lab(output: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="dispatch-pat-176-mock-") as raw_root:
        temp_root = Path(raw_root)
        scenarios = [
            await duplicate_external_event(),
            await steer_active_turn(),
            await inject_context(),
            await queued_busy_to_idle(),
            await accepted_then_ack_lost_restart_retry(temp_root),
            await queued_accept_then_crash_restart_drain(temp_root),
        ]
    report: dict[str, object] = {
        "tracking": "PAT-176",
        "lab": "mock_delivery",
        "source": "MOCK",
        "live_codex_accessed": False,
        "live_dispatch_daemon_accessed": False,
        "expected_outcomes": EXPECTED_OUTCOMES,
        "scenario_count": len(scenarios),
        "passed": sum(scenario["result"] == "pass" for scenario in scenarios),
        "failed": sum(scenario["result"] == "fail" for scenario in scenarios),
        "scenarios": scenarios,
        "conclusion": (
            "Mock evidence confirms Dispatch routing and current retry/deduplication gaps. "
            "It does not establish real provider acceptance, model behavior, visible replies, "
            "or end-to-end delivery."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".agents/notes/pat-176/mock-results.json"),
        help="Path for structured mock evidence.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(run_lab(args.output))
    print(
        json.dumps(
            {
                "output": str(args.output),
                "scenarios": report["scenario_count"],
                "passed": report["passed"],
                "failed": report["failed"],
            }
        )
    )


if __name__ == "__main__":
    main()
