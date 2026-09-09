"""Shared fake clients and evidence shaping for the PAT-176 mock lab."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tests.fakes import FakeLaneClient

from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.registry.models import MessageReceipt

DESTINATION_ID = "mock-thread-pat-176"

EXPECTED_OUTCOMES = {
    "duplicate_external_event": (
        "Replaying one external event twice starts two turns because SendInput has no "
        "external idempotency key."
    ),
    "steer_active_turn": "Steering calls turn_steer and starts no new turn.",
    "inject_context": "Context injection calls inject_items and starts no new turn.",
    "queued_busy_to_idle": (
        "A busy lane persists the message without starting a turn, then one idle drain "
        "starts one turn and marks the internal queue receipt sent."
    ),
    "accepted_then_ack_lost_restart_retry": (
        "If the provider accepts a turn but the ACK is lost, Dispatch records failure. "
        "Reopening the Registry and retrying the external event can start a second "
        "provider turn because the event identity is not retained for deduplication."
    ),
    "queued_accept_then_crash_restart_drain": (
        "If a queued message is claimed and accepted by the provider before a process "
        "crash, startup recovery resets the sending row to pending and can deliver it a "
        "second time once the lane is restored to idle."
    ),
}


class AcceptedThenAckLostClient(FakeLaneClient):
    """Record provider-side acceptance before optionally losing the synthetic ACK."""

    def __init__(self, provider_accepts: list[dict[str, str]], *, lose_ack: bool) -> None:
        super().__init__()
        self._provider_accepts = provider_accepts
        self._lose_ack = lose_ack

    async def turn_start(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        await super().turn_start(*args, **kwargs)
        text = str(args[1] if len(args) > 1 else kwargs.get("text", ""))
        self._provider_accepts.append(
            {
                "provider_turn_id": f"mock-provider-turn-{len(self._provider_accepts) + 1}",
                "text": text,
            }
        )
        if self._lose_ack:
            raise TransportError("synthetic ACK lost after provider acceptance")
        return {}


class SyntheticProcessCrash(RuntimeError):
    """Artificial process boundary injected after a fake provider acceptance."""


class AcceptedThenCrashClient(FakeLaneClient):
    """Crash after provider acceptance but before queue completion bookkeeping."""

    def __init__(self, provider_accepts: list[dict[str, str]]) -> None:
        super().__init__()
        self._provider_accepts = provider_accepts

    async def turn_start(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        await super().turn_start(*args, **kwargs)
        text = str(args[1] if len(args) > 1 else kwargs.get("text", ""))
        self._provider_accepts.append(
            {
                "provider_turn_id": f"mock-queue-provider-turn-{len(self._provider_accepts) + 1}",
                "text": text,
            }
        )
        raise SyntheticProcessCrash("synthetic crash after provider acceptance")


def turn_starts(client: FakeLaneClient) -> list[dict[str, object]]:
    return [details for name, details in client.calls if name == "turn_start"]


def calls(client: FakeLaneClient, name: str) -> list[dict[str, object]]:
    return [details for called, details in client.calls if called == name]


def receipt_evidence(receipts: Sequence[MessageReceipt]) -> list[dict[str, object]]:
    return [
        {
            "id": receipt.id,
            "queued_message_id": receipt.queued_message_id,
            "dispatch_message_id": receipt.dispatch_message_id,
            "status": receipt.status,
            "turn_id": receipt.turn_id,
            "error": receipt.error,
        }
        for receipt in receipts
    ]


def base_result(
    *,
    scenario: str,
    event_ids: list[str],
    expected_turn_count: int,
    observed_turn_count: int,
    elapsed_ms: float,
    passed: bool,
) -> dict[str, object]:
    return {
        "scenario": scenario,
        "source": "MOCK",
        "evidence_scope": "handler_registry_only",
        "source_event_ids": event_ids,
        "destination_thread_id": DESTINATION_ID,
        "expected": EXPECTED_OUTCOMES[scenario],
        "expected_turn_count": expected_turn_count,
        "observed_turn_count": observed_turn_count,
        "visible_response": None,
        "visible_response_basis": "No model or live provider was invoked.",
        "elapsed_ms": round(elapsed_ms, 3),
        "result": "pass" if passed else "fail",
    }
