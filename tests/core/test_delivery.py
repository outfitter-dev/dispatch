"""Delivery behavior through public handlers and provider evidence."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from outfitter.dispatch.contracts.errors import (
    CapabilityUnavailableError,
    DeliveryConflictError,
    ValidationError,
)
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.delivery import submit_reserved
from outfitter.dispatch.core.models import SendInput, TextContent
from outfitter.dispatch.core.providers import CodexLaneAdapter, ProviderAvailability, ProviderRouter
from outfitter.dispatch.core.turn_settings import runtime_settings_for_lane
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import AcceptedClient, LostAckClient
from tests.fakes import make_ctx


@pytest.mark.asyncio
async def test_identical_key_replay_submits_once() -> None:
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle", cwd="/tmp")
        client = AcceptedClient()
        ctx = make_ctx(store, client)
        inp = SendInput(lane="target", text="hello", idempotency_key="event-1")
        first = await handlers.send_message(inp, ctx)
        replay = await handlers.send_message(inp, ctx)
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
        assert first.delivery is not None
        assert replay.delivery is not None
        assert first.delivery.id == replay.delivery.id
        assert first.delivery.status == "accepted"
        assert first.delivery.turn_id == "turn-1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_lost_ack_retains_ambiguous_receipt_and_replay_never_resubmits() -> None:
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = LostAckClient()
        ctx = make_ctx(store, client)
        inp = SendInput(lane="target", text="hello", idempotency_key="event-1")
        first = await handlers.send_message(inp, ctx)
        replay = await handlers.send_message(inp, ctx)
        assert first.delivery is not None
        assert replay.delivery is not None
        assert first.delivery.id == replay.delivery.id
        assert replay.delivery.status == "ambiguous"
        assert "lost acknowledgment" in (replay.delivery.error or "")
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
        assert await store.lane_delivery_held("target")
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("before_ack", [True, False])
@pytest.mark.parametrize("failed", [True, False])
@pytest.mark.parametrize("legacy_ack", [True, False])
async def test_execution_state_correlates_even_when_event_precedes_ack(
    before_ack: bool, failed: bool, legacy_ack: bool
) -> None:
    from outfitter.dispatch.client.events import TurnCompleted, TurnFailed
    from outfitter.dispatch.core.reactor import Reactor
    from outfitter.dispatch.core.triggers import TriggerRunner

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = AcceptedClient()
        client.legacy_ack = legacy_ack
        ctx = make_ctx(store, client)
        reactor = Reactor(ctx, TriggerRunner(ctx, lambda: datetime.now(UTC)))

        async def emit() -> None:
            event = (
                TurnFailed("target", "turn-1", "execution failed")
                if failed
                else TurnCompleted("target", "turn-1")
            )
            await reactor.handle(event)

        if before_ack:
            client.on_accept = emit
        ack = await handlers.send_message(
            SendInput(lane="target", text="hello", idempotency_key="one"), ctx
        )
        if not before_ack:
            await emit()
        assert ack.delivery is not None
        receipt = await store.get_delivery(ack.delivery.id)
        assert receipt.status == ("accepted" if failed else "completed")
        assert receipt.execution_status == ("failed" if failed else "completed")
        assert receipt.turn_id == "turn-1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_key_replays_share_receipt_and_conflicting_reuse_fails() -> None:
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = AcceptedClient()
        ctx = make_ctx(store, client)
        acks = await asyncio.gather(
            *(
                handlers.send_message(
                    SendInput(lane="@target", text="hello", idempotency_key="one"), ctx
                )
                for _ in range(10)
            )
        )
        assert len({ack.delivery.id for ack in acks if ack.delivery is not None}) == 1
        with pytest.raises(DeliveryConflictError):
            await handlers.send_message(
                SendInput(lane="@target", text="different", idempotency_key="one"),
                ctx,
            )
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
    finally:
        await store.close()


async def test_exact_replay_precedes_selector_and_default_resolution() -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(
            id="target", handle="@target", source="own", status="idle", cwd="/original"
        )
        await store.upsert_lane_runtime_settings(
            runtime_settings_for_lane(
                lane=lane.id,
                updated_at=store.now_iso(),
                model="original-model",
            )
        )
        client = AcceptedClient()
        ctx = make_ctx(store, client)
        request = SendInput(lane="@target", text="hello", idempotency_key="stable-key")

        first = await handlers.send_message(request, ctx)
        await store.update_lane_handle(lane.id, "@moved")
        await store.add_lane(id="other", handle="@target", source="own", status="idle")
        await store.upsert_lane_runtime_settings(
            runtime_settings_for_lane(
                lane=lane.id,
                updated_at=store.now_iso(),
                model="changed-model",
            )
        )

        replay = await handlers.send_message(request, ctx)

        assert first.delivery is not None and replay.delivery is not None
        assert replay.delivery.id == first.delivery.id
        assert replay.lane == lane.id
        starts = [params for name, params in client.calls if name == "turn_start"]
        assert len(starts) == 1
        assert starts[0]["args"] == (lane.id, "hello")
        assert starts[0]["cwd"] == "/original"
        assert starts[0]["model"] == "original-model"
        stored = await store.get_delivery(first.delivery.id)
        prepared = json.loads(stored.payload)["request"]
        assert prepared["target"] == {
            "lane_id": lane.id,
            "provider": "codex",
            "binding_id": "codex-default",
            "native_session_id": lane.id,
        }
        assert prepared["correlation_id"] == stored.id
        assert prepared["settings"]["model"] == "original-model"
        with pytest.raises(DeliveryConflictError):
            await handlers.send_message(
                SendInput(lane="target", text="hello", idempotency_key="stable-key"), ctx
            )
    finally:
        await store.close()


async def test_new_keyed_structured_content_rejects_before_target_or_provider_io() -> None:
    store = await Registry.open()
    try:
        client = AcceptedClient()
        ctx = make_ctx(store, client)

        with pytest.raises(ValidationError, match=r"structured content.*idempotent delivery"):
            await handlers.send_message(
                SendInput(
                    lane="missing-target",
                    content=[TextContent(text="hello")],
                    idempotency_key="rich-key",
                ),
                ctx,
            )

        assert client.calls == []
        assert await store.get_delivery_by_key("rich-key") is None
    finally:
        await store.close()


async def test_legacy_key_replay_is_limited_to_provable_stable_input() -> None:
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        original, _ = await store.reserve_delivery(
            key="legacy-key",
            lane="target",
            mode="send",
            payload='{"cwd":"/tmp","settings":{},"text":"hello"}',
            text="hello",
        )
        client = AcceptedClient()
        ctx = make_ctx(store, client)

        replay = await handlers.send_message(
            SendInput(lane="target", text="hello", idempotency_key="legacy-key"), ctx
        )

        assert replay.delivery is not None and replay.delivery.id == original.id
        assert client.calls == []
        with pytest.raises(DeliveryConflictError, match="legacy delivery keys"):
            await handlers.send_message(
                SendInput(lane="@target", text="hello", idempotency_key="legacy-key"), ctx
            )
        with pytest.raises(DeliveryConflictError, match="legacy delivery keys"):
            await handlers.send_message(
                SendInput(lane="target", text="hello", intro=True, idempotency_key="legacy-key"),
                ctx,
            )
    finally:
        await store.close()


async def test_frozen_binding_and_current_availability_are_checked_before_submission() -> None:
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="busy")
        client = AcceptedClient()
        ctx = make_ctx(store, client)
        reserved = await handlers.send_message(
            SendInput(lane="target", text="later", mode="queue", idempotency_key="held"), ctx
        )
        assert reserved.delivery is not None and reserved.delivery.status == "queued"
        assert client.calls == []

        ctx.providers = ProviderRouter(
            (
                CodexLaneAdapter(
                    client,
                    availability=ProviderAvailability(ready=False, reason="binding stopped"),
                ),
            )
        )
        with pytest.raises(CapabilityUnavailableError, match="binding stopped"):
            await submit_reserved(reserved.delivery.id, ctx)

        receipt = await store.get_delivery(reserved.delivery.id)
        assert receipt.status == "failed"
        assert client.calls == []
    finally:
        await store.close()


async def test_legacy_prepared_request_never_adopts_a_changed_lane_binding() -> None:
    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="busy")
        receipt, _ = await store.reserve_delivery(
            key="legacy",
            lane="target",
            mode="queue",
            payload='{"cwd":"/tmp","settings":{},"text":"later"}',
            text="later",
        )
        await store._conn.execute(
            "UPDATE lanes SET provider = 'synthetic', binding_id = 'new', "
            "provider_session_id = 'native-new' WHERE id = 'target'"
        )
        await store._conn.commit()
        client = AcceptedClient()

        with pytest.raises(CapabilityUnavailableError, match="no longer matches"):
            await submit_reserved(receipt.id, make_ctx(store, client))

        assert (await store.get_delivery(receipt.id)).status == "failed"
        assert client.calls == []
    finally:
        await store.close()


@pytest.mark.parametrize("status", ["failed", "interrupted"])
@pytest.mark.parametrize("before_ack", [False, True])
async def test_nested_unsuccessful_completion_preserves_acceptance_and_execution(
    status: str,
    before_ack: bool,
) -> None:
    from outfitter.dispatch.client.events import project_notification
    from outfitter.dispatch.core.reactor import Reactor
    from outfitter.dispatch.core.triggers import TriggerRunner

    store = await Registry.open()
    try:
        await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = AcceptedClient()
        ctx = make_ctx(store, client)
        reactor = Reactor(ctx, TriggerRunner(ctx, lambda: datetime.now(UTC)))

        async def complete() -> None:
            params: dict[str, object] = {
                "threadId": "target",
                "turn": {
                    "id": "turn-1",
                    "status": status,
                    "error": {"message": "terminal error"},
                },
            }
            for event in project_notification("turn/completed", params):
                await reactor.handle(event)

        if before_ack:
            client.on_accept = complete
        ack = await handlers.send_message(
            SendInput(lane="target", text="work", idempotency_key="key"), ctx
        )
        assert ack.delivery
        if not before_ack:
            await complete()
        receipt = await store.get_delivery(ack.delivery.id)
        assert receipt.status == "accepted"
        assert receipt.execution_status == status
        assert receipt.error == "terminal error"
        assert receipt.turn_id == "turn-1"
    finally:
        await store.close()
