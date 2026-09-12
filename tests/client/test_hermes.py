"""Hermes gateway client contract over an injected in-memory transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

import pytest

from outfitter.dispatch.client.hermes import (
    HermesAttentionEvent,
    HermesCapabilityError,
    HermesClient,
    HermesEvent,
    HermesSessionCreated,
    HermesSessionCreationUnknown,
    HermesSubmissionAccepted,
    HermesSubmissionRejected,
    HermesSubmissionUnknown,
)
from tests.client.conftest import FakeTransport


def _ready(epoch: str = "epoch-1") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "gateway.ready", "payload": {"replay_epoch": epoch}},
    }


def _event(
    event_type: str,
    runtime_session_id: str,
    turn_id: str,
    **payload: object,
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": event_type,
            "session_id": runtime_session_id,
            "payload": {"turn_id": turn_id, **payload},
        },
    }


def _attention_event(
    event_type: str,
    runtime_session_id: str,
    request_id: object = "request-1",
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": event_type,
            "session_id": runtime_session_id,
            "payload": {"request_id": request_id},
        },
    }


def _capability_responder(
    *,
    if_idle: bool = True,
    correlation: bool = True,
    extra: Callable[[dict[str, object]], list[dict[str, object]]] | None = None,
) -> Callable[[dict[str, object]], list[dict[str, object]]]:
    def respond(request: dict[str, object]) -> list[dict[str, object]]:
        if request["method"] == "gateway.capabilities":
            return [
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {
                        "prompt_submit_if_idle_v1": if_idle,
                        "prompt_turn_correlation_v1": correlation,
                    },
                }
            ]
        return extra(request) if extra is not None else []

    return respond


async def _negotiated_client(
    transport: FakeTransport,
    *,
    responder: Callable[[dict[str, object]], list[dict[str, object]]] | None = None,
    max_pre_ack_events: int = 64,
    max_pre_ack_bytes: int = 256 * 1024,
) -> HermesClient:
    transport.auto = _capability_responder(extra=responder)
    client = HermesClient(
        transport,
        request_timeout=1,
        max_pre_ack_events=max_pre_ack_events,
        max_pre_ack_bytes=max_pre_ack_bytes,
    )
    await client.start()
    transport.feed(_ready())
    capabilities = await client.negotiate()
    assert capabilities.replay_epoch == "epoch-1"
    return client


async def test_negotiate_requires_both_exact_capabilities() -> None:
    for if_idle, correlation, missing in (
        (False, True, "prompt_submit_if_idle_v1"),
        (True, False, "prompt_turn_correlation_v1"),
    ):
        transport = FakeTransport()
        transport.auto = _capability_responder(if_idle=if_idle, correlation=correlation)
        client = HermesClient(transport, request_timeout=1)
        await client.start()
        transport.feed(_ready("native-epoch"))
        try:
            with pytest.raises(HermesCapabilityError, match=missing):
                await client.negotiate()
        finally:
            await client.close()


async def test_create_parses_runtime_stored_and_effective_cwd() -> None:
    transport = FakeTransport()

    def create(request: dict[str, object]) -> list[dict[str, object]]:
        assert request["method"] == "session.create"
        assert request["params"] == {
            "profile": "default",
            "cwd": "/tmp/project",
            "title": "Synthetic",
        }
        return [
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "session_id": "runtime-1",
                    "stored_session_id": "stored-1",
                    "info": {"cwd": "/tmp/project"},
                },
            }
        ]

    client = await _negotiated_client(transport, responder=create)
    try:
        result = await client.create_session(
            profile="default", cwd="/tmp/project", title="Synthetic"
        )
        assert result == HermesSessionCreated(
            runtime_session_id="runtime-1",
            stored_session_id="stored-1",
            effective_cwd="/tmp/project",
        )
    finally:
        await client.close()


@pytest.mark.parametrize(
    "reply",
    [
        {"result": {"session_id": "runtime-only"}},
        {"error": {"code": -32602, "message": "invalid params"}},
    ],
)
async def test_create_errors_after_entry_are_unknown(reply: dict[str, object]) -> None:
    transport = FakeTransport()

    def create(request: dict[str, object]) -> list[dict[str, object]]:
        return [{"jsonrpc": "2.0", "id": request["id"], **reply}]

    client = await _negotiated_client(transport, responder=create)
    try:
        result = await client.create_session(profile="default", cwd="/tmp", title="Synthetic")
        assert isinstance(result, HermesSessionCreationUnknown)
    finally:
        await client.close()


async def test_submit_uses_strict_idle_and_binds_events_that_precede_ack() -> None:
    transport = FakeTransport()

    def submit(request: dict[str, object]) -> list[dict[str, object]]:
        assert request["params"] == {
            "session_id": "runtime-1",
            "text": "hello",
            "if_idle": True,
        }
        return [
            _event("message.start", "runtime-1", "turn-1"),
            _event("message.complete", "runtime-1", "turn-1", status="complete", text="hi"),
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"status": "streaming", "turn_id": "turn-1"},
            },
        ]

    client = await _negotiated_client(transport, responder=submit)
    try:
        result = await client.submit_prompt(runtime_session_id="runtime-1", text="hello")
        assert isinstance(result, HermesSubmissionAccepted)
        assert result.turn_id == "turn-1"
        assert result.partial is False
        assert [event.type for event in result.stream.buffered] == [
            "message.start",
            "message.complete",
        ]

        transport.feed(_event("session.info", "runtime-1", "turn-1", running=False))
        assert (await result.stream.__anext__()).type == "session.info"
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            {"code": 4091, "message": "session is busy", "data": {"reason": "busy"}},
            HermesSubmissionRejected,
        ),
        (
            {"code": 4091, "message": "session is busy", "data": {}},
            HermesSubmissionUnknown,
        ),
        (
            {"code": -32602, "message": "invalid params"},
            HermesSubmissionUnknown,
        ),
    ],
)
async def test_only_exact_strict_busy_error_is_a_definite_rejection(
    error: dict[str, object],
    expected: type[HermesSubmissionRejected] | type[HermesSubmissionUnknown],
) -> None:
    transport = FakeTransport()

    def submit(request: dict[str, object]) -> list[dict[str, object]]:
        return [{"jsonrpc": "2.0", "id": request["id"], "error": error}]

    client = await _negotiated_client(transport, responder=submit)
    try:
        result = await client.submit_prompt(runtime_session_id="runtime-1", text="hello")
        assert isinstance(result, expected)
    finally:
        await client.close()


async def test_transport_failure_after_submit_entry_is_unknown() -> None:
    class FailingTransport(FakeTransport):
        fail = False

        async def send(self, message: Mapping[str, object]) -> None:
            if self.fail:
                raise OSError("broken pipe")
            await super().send(message)

    transport = FailingTransport()
    client = await _negotiated_client(transport)
    transport.fail = True
    try:
        result = await client.submit_prompt(runtime_session_id="runtime-1", text="hello")
        assert isinstance(result, HermesSubmissionUnknown)
        assert "broken pipe" in result.error
    finally:
        await client.close()


async def test_eof_after_submit_entry_is_unknown() -> None:
    transport = FakeTransport()

    def submit(_request: dict[str, object]) -> list[dict[str, object]]:
        transport.eof()
        return []

    client = await _negotiated_client(transport, responder=submit)
    try:
        result = await client.submit_prompt(runtime_session_id="runtime-1", text="hello")
        assert isinstance(result, HermesSubmissionUnknown)
        assert "stream closed" in result.error
    finally:
        await client.close()


@pytest.mark.parametrize(
    "result",
    [
        {"status": "streaming"},
        {"status": "queued", "turn_id": "turn-1"},
        {"status": "streaming", "turn_id": ""},
    ],
)
async def test_malformed_or_idless_prompt_ack_is_unknown(result: dict[str, object]) -> None:
    transport = FakeTransport()

    def submit(request: dict[str, object]) -> list[dict[str, object]]:
        return [{"jsonrpc": "2.0", "id": request["id"], "result": result}]

    client = await _negotiated_client(transport, responder=submit)
    try:
        outcome = await client.submit_prompt(runtime_session_id="runtime-1", text="hello")
        assert isinstance(outcome, HermesSubmissionUnknown)
    finally:
        await client.close()


async def test_pre_ack_overflow_is_explicit_partial_uncertainty() -> None:
    transport = FakeTransport()

    def submit(request: dict[str, object]) -> list[dict[str, object]]:
        return [
            _event("message.start", "runtime-1", "turn-1", sequence=1),
            _event("message.delta", "runtime-1", "turn-1", sequence=2),
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"status": "streaming", "turn_id": "turn-1"},
            },
        ]

    client = await _negotiated_client(
        transport,
        responder=submit,
        max_pre_ack_events=1,
        max_pre_ack_bytes=1_000,
    )
    try:
        result = await client.submit_prompt(runtime_session_id="runtime-1", text="hello")
        assert isinstance(result, HermesSubmissionAccepted)
        assert result.partial is True
        assert result.uncertainty_reason == "Hermes events exceeded the bounded pre-ACK buffer"
        assert [event.payload["sequence"] for event in result.stream.buffered] == [2]
    finally:
        await client.close()


async def test_pre_ack_events_are_isolated_by_runtime_and_turn_identity() -> None:
    transport = FakeTransport()
    activity: list[HermesEvent] = []

    def submit(request: dict[str, object]) -> list[dict[str, object]]:
        return [
            _event("message.complete", "other-runtime", "turn-1", text="wrong runtime"),
            _event("message.complete", "runtime-1", "other-turn", text="wrong turn"),
            _event("message.start", "runtime-1", "turn-1"),
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"status": "streaming", "turn_id": "turn-1"},
            },
        ]

    client = await _negotiated_client(transport, responder=submit)
    client.set_activity_handler(activity.append)
    try:
        result = await client.submit_prompt(runtime_session_id="runtime-1", text="hello")
        assert isinstance(result, HermesSubmissionAccepted)
        assert [event.type for event in result.stream.buffered] == ["message.start"]
        assert [(event.runtime_session_id, event.turn_id) for event in activity] == [
            ("other-runtime", "turn-1"),
            ("runtime-1", "other-turn"),
            ("runtime-1", "turn-1"),
        ]
    finally:
        await client.close()


async def test_known_blocking_requests_reach_attention_without_turn_attribution() -> None:
    transport = FakeTransport()
    observed: list[HermesAttentionEvent] = []
    client = await _negotiated_client(transport)
    client.set_attention_handler(observed.append)
    try:
        for family in (
            "clarify",
            "approval",
            "terminal.read",
            "preview.read",
            "preview.act",
            "window.read",
            "mcp.setup",
            "tour",
            "sudo",
            "secret",
            "vault.unlock",
            "vault.save_login",
            "vault.code",
        ):
            transport.feed(_attention_event(f"{family}.request", "runtime-1", family))
        await asyncio.sleep(0)

        assert [event.family for event in observed] == [
            "clarify",
            "approval",
            "terminal.read",
            "preview.read",
            "preview.act",
            "window.read",
            "mcp.setup",
            "tour",
            "sudo",
            "secret",
            "vault.unlock",
            "vault.save_login",
            "vault.code",
        ]
        assert all(event.runtime_session_id == "runtime-1" for event in observed)
        assert all(event.expired is False for event in observed)
    finally:
        await client.close()


async def test_attention_parser_fences_telemetry_and_only_forwards_proven_expiry() -> None:
    transport = FakeTransport()
    observed: list[HermesAttentionEvent] = []
    client = await _negotiated_client(transport)
    client.set_attention_handler(observed.append)
    try:
        transport.feed(_attention_event("unknown.request", "runtime-1"))
        transport.feed(_attention_event("approval.expire", "runtime-1"))
        transport.feed(_attention_event("clarify.expire", "runtime-1", ""))
        transport.feed(_event("thinking.delta", "runtime-1", "turn-1"))
        transport.feed(_attention_event("clarify.request", "runtime-1", 42))
        transport.feed(_attention_event("clarify.expire", "runtime-1", "request-1"))
        await asyncio.sleep(0)

        assert [(event.type, event.request_id) for event in observed] == [
            ("clarify.request", None),
            ("clarify.expire", "request-1"),
        ]
    finally:
        await client.close()
