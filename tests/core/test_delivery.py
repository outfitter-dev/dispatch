"""Delivery behavior through public handlers and provider evidence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import SendInput
from outfitter.dispatch.core.providers import (
    CodexLaneAdapter,
    ProviderAction,
    ProviderAvailability,
    ProviderBindingFacts,
    ProviderDurability,
    ProviderRouter,
)
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import AcceptedClient, LostAckClient
from tests.fakes import make_ctx
from tests.fixtures.registry.builders import thread_turn


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
async def test_execution_join_uses_provider_qualified_routed_identity() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: AcceptedClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset({ProviderAction.SEND}),
                    availability=ProviderAvailability(ready=True),
                    durability=ProviderDurability(),
                )

        await store.add_lane(
            id="dsp_target",
            handle="@target",
            source="own",
            status="idle",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_thread_id="native-target",
        )
        client = AcceptedClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))

        async def complete_before_ack() -> None:
            turn = thread_turn(
                lane="dsp_target",
                binding_id="synthetic-binding",
                provider_thread_id="native-target",
                turn_id="turn-1",
                status="completed",
            ).model_copy(update={"provider": "synthetic"})
            await store.upsert_thread_turn(turn)

        client.on_accept = complete_before_ack

        ack = await handlers.send_message(
            SendInput(lane="dsp_target", text="hello", idempotency_key="one"), ctx
        )

        assert ack.delivery is not None
        receipt = await store.get_delivery(ack.delivery.id)
        assert receipt.status == "completed"
        assert receipt.execution_status == "completed"
        assert receipt.turn_id == "turn-1"
        turn_calls = [call for call in client.calls if call[0] == "turn_start"]
        args = turn_calls[0][1]["args"]
        assert isinstance(args, tuple)
        assert args[0] == "native-target"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_key_replays_share_receipt_and_conflicting_reuse_fails() -> None:
    from outfitter.dispatch.contracts.errors import DeliveryConflictError

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
        same = await handlers.send_message(
            SendInput(lane="target", text="hello", idempotency_key="one"),
            ctx,
        )
        assert same.delivery is not None and same.delivery.status == "accepted"
        with pytest.raises(DeliveryConflictError):
            await handlers.send_message(
                SendInput(lane="target", text="different", idempotency_key="one"),
                ctx,
            )
        assert len([call for call in client.calls if call[0] == "turn_start"]) == 1
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
