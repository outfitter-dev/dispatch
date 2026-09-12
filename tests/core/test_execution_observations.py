"""Provider-neutral execution evidence and selected Codex producers."""

from __future__ import annotations

from datetime import UTC, datetime

from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.delivery_reconciliation import _refresh_idle_readiness
from outfitter.dispatch.core.providers import (
    CodexLaneAdapter,
    ProviderAction,
    ProviderAvailability,
    ProviderRouter,
)
from outfitter.dispatch.registry.observations import ProviderCorrelation, ProviderObservation
from outfitter.dispatch.registry.store import Registry
from tests.core.delivery_fakes import HistoryClient
from tests.fakes import make_ctx


def test_hermes_shaped_observation_keeps_bounded_provider_evidence() -> None:
    observed = ProviderObservation(
        provider="hermes",
        binding_id="local",
        native_session_id="conversation-1",
        kind="completed",
        correlation=ProviderCorrelation(
            delivery_id="receipt-1",
            correlation_id="receipt-1",
            native_run_id="run-1",
        ),
        generation="connection-2",
        source="history",
        provider_time=datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
        received_at=datetime(2026, 9, 12, 10, 1, tzinfo=UTC),
        partial=False,
    )

    assert observed.correlation.native_run_id == "run-1"
    assert observed.source == "history"


async def test_readiness_from_replaced_generation_cannot_mark_lane_idle() -> None:
    store = await Registry.open()

    class ReconnectingClient(HistoryClient):
        async def thread_read(
            self, thread_id: str, include_turns: bool = False
        ) -> dict[str, object]:
            ctx.provider_session_id = "generation-2"
            ctx.providers = ProviderRouter.default_codex(self, generation="generation-2")
            return {"thread": {"id": thread_id, "status": {"type": "idle"}}}

    try:
        await store.add_lane(id="target", handle="@target", source="own", status="busy")
        client = ReconnectingClient()
        ctx = make_ctx(store, client)
        ctx.provider_session_id = "generation-1"
        ctx.providers = ProviderRouter(
            (
                CodexLaneAdapter(
                    client,
                    availability=ProviderAvailability(ready=True, generation="generation-1"),
                ),
            )
        )

        assert await _refresh_idle_readiness("target", ctx) is False
        assert (await store.get_lane("target")).status == "busy"
    finally:
        await store.close()


async def test_lane_read_keeps_support_separate_from_current_readiness() -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(id="target", handle="@target", source="own", status="idle")
        client = HistoryClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter(
            (
                CodexLaneAdapter(
                    client,
                    availability=ProviderAvailability(
                        ready=False, reason="connection unavailable", generation="generation-1"
                    ),
                    supported_actions=frozenset({ProviderAction.READ, ProviderAction.SEND}),
                ),
            )
        )

        state = handlers._ref(lane, ctx).provider_state

        assert state.supported_actions == ["read", "send"]
        assert state.readiness == "unavailable"
        assert state.readiness_reason == "connection unavailable"
        assert state.generation == "generation-1"
        assert state.source == "runtime_binding"
        assert state.as_of is None
        assert state.partial is False
        assert state.uncertain is False
    finally:
        await store.close()
