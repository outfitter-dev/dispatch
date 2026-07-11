from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from outfitter.dispatch.client.events import ServerRequestReceived, classify_server_request
from outfitter.dispatch.client.models import JsonRpcError, JsonRpcId
from outfitter.dispatch.config import RuntimePolicy
from outfitter.dispatch.contracts.errors import ValidationError
from outfitter.dispatch.core.server_request_policy import (
    automatic_response,
    expected_response,
    validate_operator_response,
)
from outfitter.dispatch.core.server_requests import (
    ServerRequestManager,
    respond_to_server_request,
)
from outfitter.dispatch.registry.models import Subscription
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    registry = await Registry.open()
    try:
        yield registry
    finally:
        await registry.close()


async def test_owned_user_input_becomes_durable_attention(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@worker", source="own", status="busy")
    await store.add_lane(id="S1", handle="@supervisor", source="own", status="idle")
    now = datetime(2026, 7, 10, tzinfo=UTC)
    await store.add_subscription(
        Subscription(
            id="sub-attention",
            target_lane="L1",
            subscriber_lane="S1",
            when="needs-attention",
            delivery="inbox",
            deliver="idle",
            tail=0,
            once=False,
            ack="manual",
            created_at=now,
            updated_at=now,
        )
    )
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    ctx.provider_session_id = "session-1"
    manager = ServerRequestManager(ctx)

    request = await manager.handle(
        ServerRequestReceived(
            method="item/tool/requestUserInput",
            request_id="question-7",
            category="user_input",
            thread_id="L1",
            turn_id="T1",
            item_id="I1",
            raw_params={
                "questions": [
                    {
                        "id": "choice",
                        "header": "Pick",
                        "question": "Which path?",
                        "options": [{"label": "A", "description": "First"}],
                    }
                ]
            },
        )
    )

    assert request.id is not None
    assert request.state == "pending"
    assert (await store.get_lane("L1")).status == "waiting_input"
    runtime = await store.get_lane_runtime_state("L1")
    assert runtime is not None
    assert runtime.needs_attention is True
    assert runtime.attention_kind == "user_input"
    inbox = await store.list_inbox_messages(lane="L1")
    assert inbox[0].payload["request_id"] == request.id
    assert inbox[0].payload["questions"][0]["question"] == "Which path?"  # type: ignore[index]
    assert not any(name == "respond_server_request" for name, _ in client.calls)
    subscriber_inbox = await store.list_inbox_messages(lane="S1")
    assert subscriber_inbox[0].payload["event"] == "attention"
    events = await store.list_provider_events(lane="L1")
    assert events[0].event_type == "server_request.received"
    assert events[0].summary["category"] == "user_input"
    await manager.close()


async def test_attached_request_is_denied_by_default(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@desktop", source="attached", status="busy")
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    ctx.provider_session_id = "session-1"
    manager = ServerRequestManager(ctx)

    request = await manager.handle(
        ServerRequestReceived(
            method="item/commandExecution/requestApproval",
            request_id=7,
            category="approval",
            thread_id="L1",
        )
    )

    stored = await store.get_server_request_by_id(request.id or 0)
    assert stored is not None
    assert stored.state == "denied"
    assert any(
        name == "respond_server_request" and call["result"] == {"decision": "decline"}
        for name, call in client.calls
    )
    assert {event.event_type for event in await store.list_provider_events(lane="L1")} == {
        "server_request.received",
        "server_request.denied",
    }
    await manager.close()


async def test_permissive_owned_permission_request_grants_requested_profile(
    store: Registry,
) -> None:
    await store.add_lane(id="L1", handle="@worker", source="own", status="busy")
    client = FakeLaneClient()
    ctx = make_ctx(
        store,
        client,
        policy=RuntimePolicy(owned_interactive_requests="permissive"),
    )
    ctx.provider_session_id = "session-1"
    manager = ServerRequestManager(ctx)

    request = await manager.handle(
        ServerRequestReceived(
            method="item/permissions/requestApproval",
            request_id=8,
            category="approval",
            thread_id="L1",
            raw_params={"permissions": {"network": {"enabled": True}}},
        )
    )

    stored = await store.get_server_request_by_id(request.id or 0)
    assert stored is not None
    assert stored.state == "responded"
    assert any(
        name == "respond_server_request"
        and call["result"] == {"permissions": {"network": {"enabled": True}}, "scope": "turn"}
        for name, call in client.calls
    )
    await manager.close()


async def test_threadless_auth_request_gets_explicit_error_without_secret_storage(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    ctx.provider_session_id = "session-1"
    manager = ServerRequestManager(ctx)

    request = await manager.handle(
        ServerRequestReceived(
            method="account/chatgptAuthTokens/refresh",
            request_id="auth-1",
            category="auth",
            raw_params={"accessToken": "must-not-persist"},
        )
    )

    stored = await store.get_server_request_by_id(request.id or 0)
    assert stored is not None
    assert stored.state == "failed"
    assert "must-not-persist" not in stored.model_dump_json()
    response = next(call for name, call in client.calls if name == "respond_server_request")
    assert response["error"]["code"] == -32041  # type: ignore[index]
    await manager.close()


async def test_operator_response_is_validated_and_sent_once(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@worker", source="own", status="busy")
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    ctx.provider_session_id = "session-1"
    manager = ServerRequestManager(ctx)
    pending = await manager.handle(
        ServerRequestReceived(
            method="mcpServer/elicitation/request",
            request_id=9,
            category="elicitation",
            thread_id="L1",
            raw_params={"serverName": "calendar"},
        )
    )

    completed = await respond_to_server_request(
        ctx, pending.id or 0, {"action": "accept", "content": {"date": "tomorrow"}}
    )

    assert completed.state == "responded"
    responses = [call for name, call in client.calls if name == "respond_server_request"]
    assert len(responses) == 1
    with pytest.raises(ValidationError, match="already responded"):
        await respond_to_server_request(ctx, pending.id or 0, {"action": "decline"})
    assert len([call for name, call in client.calls if name == "respond_server_request"]) == 1
    await manager.close()


async def test_duplicate_delivery_does_not_duplicate_attention_or_response(
    store: Registry,
) -> None:
    await store.add_lane(id="L1", handle="@worker", source="own", status="busy")
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    ctx.provider_session_id = "session-1"
    manager = ServerRequestManager(ctx)
    event = ServerRequestReceived(
        method="item/tool/requestUserInput",
        request_id=99,
        category="user_input",
        thread_id="L1",
    )

    first = await manager.handle(event)
    duplicate = await manager.handle(event)

    assert duplicate.id == first.id
    assert len(await store.list_inbox_messages(lane="L1")) == 1
    await respond_to_server_request(ctx, first.id or 0, {"answers": {}})
    await manager.handle(event)
    assert len(await store.list_inbox_messages(lane="L1")) == 1
    assert len([call for name, call in client.calls if name == "respond_server_request"]) == 1
    await manager.close()


async def test_attention_timeout_sends_safe_terminal_response(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@worker", source="own", status="busy")
    client = FakeLaneClient()
    ctx = make_ctx(
        store,
        client,
        policy=RuntimePolicy(interactive_request_timeout_seconds=60),
    )
    ctx.provider_session_id = "session-1"
    manager = ServerRequestManager(ctx)
    request = await manager.handle(
        ServerRequestReceived(
            method="item/tool/requestUserInput",
            request_id=10,
            category="user_input",
            thread_id="L1",
        )
    )

    await manager.expire(request.id or 0)
    stored = await store.get_server_request_by_id(request.id or 0)
    assert stored is not None
    assert stored.state == "timed_out"
    response = next(call for name, call in client.calls if name == "respond_server_request")
    assert response["error"]["code"] == -32042  # type: ignore[index]
    await manager.close()


async def test_reconnect_fails_stale_request_and_clears_attention(store: Registry) -> None:
    await store.add_lane(id="L1", handle="@worker", source="own", status="busy")
    old_ctx = make_ctx(store, FakeLaneClient())
    old_ctx.provider_session_id = "old-session"
    old_manager = ServerRequestManager(old_ctx)
    pending = await old_manager.handle(
        ServerRequestReceived(
            method="item/tool/requestUserInput",
            request_id=11,
            category="user_input",
            thread_id="L1",
        )
    )
    await old_manager.close()
    assert (await store.get_lane("L1")).status == "waiting_input"

    new_ctx = make_ctx(store, FakeLaneClient())
    new_ctx.provider_session_id = "new-session"
    await ServerRequestManager(new_ctx).run()

    failed = await store.get_server_request_by_id(pending.id or 0)
    assert failed is not None
    assert failed.state == "failed"
    assert (await store.get_lane("L1")).status == "idle"
    runtime = await store.get_lane_runtime_state("L1")
    assert runtime is not None
    assert runtime.needs_attention is False


async def test_response_send_failure_is_audited_and_clears_attention(store: Registry) -> None:
    class FailingClient(FakeLaneClient):
        async def respond_server_request(
            self,
            request_id: JsonRpcId,
            *,
            result: Mapping[str, object] | None = None,
            error: JsonRpcError | None = None,
        ) -> None:
            raise RuntimeError("wire closed")

    await store.add_lane(id="L1", handle="@worker", source="own", status="busy")
    client = FailingClient()
    ctx = make_ctx(store, client)
    ctx.provider_session_id = "session-1"
    manager = ServerRequestManager(ctx)
    pending = await manager.handle(
        ServerRequestReceived(
            method="item/tool/requestUserInput",
            request_id=12,
            category="user_input",
            thread_id="L1",
        )
    )

    with pytest.raises(RuntimeError, match="wire closed"):
        await respond_to_server_request(ctx, pending.id or 0, {"answers": {}})

    failed = await store.get_server_request_by_id(pending.id or 0)
    assert failed is not None
    assert failed.state == "failed"
    assert (await store.get_lane("L1")).status == "idle"
    actions = await store.recent_actions(limit=5)
    assert actions[0].outcome == "error"
    await manager.close()


def test_operator_response_validation_rejects_wrong_shape() -> None:
    with pytest.raises(ValidationError, match="fields"):
        validate_operator_response("item/tool/requestUserInput", {"answer": "yes"})
    with pytest.raises(ValidationError, match="host handler"):
        validate_operator_response("attestation/generate", {"token": "nope"})
    with pytest.raises(ValidationError, match="answers list"):
        validate_operator_response(
            "item/tool/requestUserInput",
            {"answers": {"choice": {"answer": "yes"}}},
        )
    with pytest.raises(ValidationError, match="invalid shape"):
        validate_operator_response(
            "item/tool/call",
            {"contentItems": [{"type": "inputText", "imageUrl": "wrong"}], "success": True},
        )

    assert (
        validate_operator_response(
            "item/tool/call",
            {"contentItems": [{"type": "inputText", "text": "done"}], "success": True},
        )["success"]
        is True
    )


@pytest.mark.parametrize(
    "method",
    [
        "account/chatgptAuthTokens/refresh",
        "applyPatchApproval",
        "attestation/generate",
        "execCommandApproval",
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/call",
        "item/tool/requestUserInput",
        "mcpServer/elicitation/request",
    ],
)
def test_every_manifest_request_has_a_safe_outcome_or_operator_contract(method: str) -> None:
    request = ServerRequestReceived(
        method=method,
        request_id=1,
        category=classify_server_request(method),
        thread_id="L1",
        raw_params={"permissions": {}},
    )

    assert automatic_response(request, mode="deny", actionable=True) is not None
    attention = automatic_response(request, mode="attention", actionable=True)
    assert attention is not None or expected_response(method) is not None
