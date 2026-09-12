"""Exact provider routing, support, availability, and generation tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from outfitter.dispatch.client.models import ThreadInfo
from outfitter.dispatch.contracts.errors import CapabilityUnavailableError
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import (
    ForkInput,
    LaneSyncInput,
    LaneTextInput,
    ShowInput,
    ThreadTargetInput,
)
from outfitter.dispatch.core.providers import (
    CodexLaneAdapter,
    ProviderAction,
    ProviderAvailability,
    ProviderBindingFacts,
    ProviderDurability,
    ProviderRouter,
)
from outfitter.dispatch.registry.models import Lane
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID, Registry
from tests.fakes import FakeLaneClient, make_ctx


def _lane(**changes: object) -> Lane:
    values: dict[str, object] = {
        "id": "native-codex",
        "provider": "codex",
        "binding_id": DEFAULT_CODEX_BINDING_ID,
        "provider_session_id": "native-codex",
        "ref": "0abc",
        "ref_source": "0",
        "ref_payload": "abc",
        "ref_mixer": "1",
        "handle": "@lane",
        "source": "own",
        "created_at": datetime(2026, 9, 12, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 12, tzinfo=UTC),
    }
    values.update(changes)
    return Lane.model_validate(values)


def test_default_codex_route_fixes_exact_native_identity_and_separate_facts() -> None:
    client = FakeLaneClient()
    durability = ProviderDurability(local_reservation=True, native_evidence=True)
    adapter = CodexLaneAdapter(
        client,
        availability=ProviderAvailability(ready=True, generation="generation-1"),
        durability=durability,
    )
    route = ProviderRouter((adapter,)).route_lane(_lane(), ProviderAction.SEND)

    assert route.target.native_session_id == "native-codex"
    assert route.target.binding_id == DEFAULT_CODEX_BINDING_ID
    assert route.availability.ready is True
    assert route.durability == durability
    route.recheck("generation-1")
    with pytest.raises(CapabilityUnavailableError, match="generation changed"):
        route.recheck("generation-2")


def test_route_never_falls_back_for_missing_binding_or_native_identity() -> None:
    router = ProviderRouter.default_codex(FakeLaneClient())

    with pytest.raises(CapabilityUnavailableError, match="not registered"):
        router.route_lane(
            _lane(
                id="dsp_other",
                provider="hermes",
                binding_id="default",
                provider_session_id="native-codex",
            ),
            ProviderAction.READ,
        )
    with pytest.raises(CapabilityUnavailableError, match="no provider session identity"):
        router.route_lane(
            _lane(
                id="dsp_reserved", provider="hermes", binding_id="default", provider_session_id=None
            ),
            ProviderAction.SEND,
        )


def test_unsupported_and_unavailable_bindings_are_distinct() -> None:
    client = FakeLaneClient()
    unsupported = ProviderRouter(
        (CodexLaneAdapter(client, supported_actions=frozenset({ProviderAction.READ})),)
    )
    with pytest.raises(CapabilityUnavailableError, match="unsupported"):
        unsupported.route_lane(_lane(), ProviderAction.SEND)

    unavailable = ProviderRouter(
        (
            CodexLaneAdapter(
                client,
                availability=ProviderAvailability(ready=False, reason="runtime stopped"),
            ),
        )
    )
    with pytest.raises(CapabilityUnavailableError, match="runtime stopped"):
        unavailable.route_lane(_lane(), ProviderAction.READ)


def test_launch_is_explicit_default_codex_and_never_provider_fallback() -> None:
    router = ProviderRouter.default_codex(FakeLaneClient())
    route = router.route_launch("codex", ProviderAction.LAUNCH)
    assert route.binding_id == DEFAULT_CODEX_BINDING_ID
    with pytest.raises(CapabilityUnavailableError, match="no registered launch binding"):
        router.route_launch("hermes", ProviderAction.LAUNCH)


async def test_action_support_projects_capabilities_and_blocks_before_client_io() -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(id="native-codex", handle="@lane", source="own")
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter(
            (CodexLaneAdapter(client, supported_actions=frozenset({ProviderAction.READ})),)
        )

        view = await handlers.show(ShowInput(lane=lane.ref), ctx)
        assert view.capabilities.read is True
        assert view.capabilities.send is False
        with pytest.raises(CapabilityUnavailableError, match="send is unsupported"):
            await handlers.send(LaneTextInput(lane=lane.ref, text="blocked"), ctx)
        assert not client.calls
    finally:
        await store.close()


async def test_generation_guard_precedes_lane_and_receipt_mutation() -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(id="native-codex", handle="@lane", source="own", status="idle")
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.provider_session_id = "generation-2"
        ctx.providers = ProviderRouter(
            (
                CodexLaneAdapter(
                    client,
                    availability=ProviderAvailability(ready=True, generation="generation-1"),
                ),
            )
        )

        with pytest.raises(CapabilityUnavailableError, match="generation changed"):
            await handlers.send(LaneTextInput(lane=lane.ref, text="blocked"), ctx)
        assert (await store.get_lane(lane.id)).status == "idle"
        assert await store.list_message_receipts(lane=lane.id) == []
        assert not client.calls
    finally:
        await store.close()


async def test_sync_rechecks_generation_before_delayed_history_io() -> None:
    store = await Registry.open()
    try:

        class ReconnectingClient(FakeLaneClient):
            after_read: Callable[[], None]

            async def thread_read(
                self, thread_id: str, include_turns: bool = False
            ) -> dict[str, object]:
                result = await super().thread_read(thread_id, include_turns)
                self.after_read()
                return result

        stale = ReconnectingClient()
        stale.read_result = {"thread": {"id": "native-codex"}}
        fresh = FakeLaneClient()
        ctx = make_ctx(store, stale)
        ctx.provider_session_id = "generation-1"
        ctx.providers = ProviderRouter.default_codex(stale, generation="generation-1")

        def reconnect() -> None:
            ctx.provider_session_id = "generation-2"
            ctx.providers = ProviderRouter.default_codex(fresh, generation="generation-2")

        stale.after_read = reconnect
        lane = await store.add_lane(id="native-codex", handle="@lane", source="own")

        with pytest.raises(CapabilityUnavailableError, match="generation changed"):
            await handlers.sync_lane(LaneSyncInput(lane=lane.ref), ctx)

        assert [name for name, _ in stale.calls] == ["thread_read"]
        assert fresh.calls == []
    finally:
        await store.close()


async def test_managed_lifecycle_writes_use_exact_nondefault_route_identity() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: FakeLaneClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset(
                        {
                            ProviderAction.SYNC,
                            ProviderAction.ARCHIVE,
                            ProviderAction.RESTORE,
                        }
                    ),
                    availability=ProviderAvailability(ready=True),
                    durability=ProviderDurability(),
                )

        client = FakeLaneClient()
        client.read_result = {"thread": {"id": "native-session"}}
        client.list_results_by_archived[True] = [ThreadInfo(id="native-session")]
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))
        lane = await store.add_lane(
            id="dsp_synthetic",
            handle="@synthetic",
            source="own",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_session_id="native-session",
        )

        synced = await handlers._reconcile_archive_membership(lane, ctx)
        assert synced.status == "archived"
        observed = await store.get_provider_thread(
            "synthetic", "native-session", binding_id="synthetic-binding"
        )
        assert observed is not None and observed.lifecycle_state == "archived"

        restored = await handlers.restore(ThreadTargetInput(target=lane.ref), ctx)
        assert restored.status == "unknown"
        observed = await store.get_provider_thread(
            "synthetic", "native-session", binding_id="synthetic-binding"
        )
        assert observed is not None and observed.lifecycle_state == "active"

        await handlers.archive(ThreadTargetInput(target=lane.ref), ctx)
        observed = await store.get_provider_thread(
            "synthetic", "native-session", binding_id="synthetic-binding"
        )
        assert observed is not None and observed.lifecycle_state == "archived"
        assert await store.get_provider_thread("codex", "dsp_synthetic") is None
        assert await store.get_provider_thread("codex", "native-session") is None
    finally:
        await store.close()


async def test_managed_topology_observations_use_exact_nondefault_route_identity() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: FakeLaneClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset({ProviderAction.TOPOLOGY}),
                    availability=ProviderAvailability(ready=True),
                    durability=ProviderDurability(),
                )

        client = FakeLaneClient()
        client.read_result = {"thread": {"id": "native-root"}}
        client.list_result = [ThreadInfo(id="native-child", parent_thread_id="native-root")]
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))
        lane = await store.add_lane(
            id="dsp_root",
            handle="@root",
            source="own",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_session_id="native-root",
        )

        detail = await handlers.show(ShowInput(lane=lane.ref, topology=True), ctx)

        assert [child.id for child in detail.topology.children] == ["native-child"]
        assert await store.get_provider_thread(
            "synthetic", "native-root", binding_id="synthetic-binding"
        )
        assert await store.get_provider_thread(
            "synthetic", "native-child", binding_id="synthetic-binding"
        )
        assert await store.get_provider_thread("codex", "native-root") is None
        assert await store.get_provider_thread("codex", "native-child") is None
    finally:
        await store.close()


async def test_nondefault_fork_is_unavailable_before_provider_or_registry_effects() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: FakeLaneClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset({ProviderAction.READ, ProviderAction.FORK}),
                    availability=ProviderAvailability(ready=True),
                    durability=ProviderDurability(),
                )

        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))
        lane = await store.add_lane(
            id="dsp_root",
            handle="@root",
            source="own",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_session_id="native-root",
        )

        detail = await handlers.show(ShowInput(lane=lane.ref), ctx)
        assert detail.capabilities.fork is False
        with pytest.raises(CapabilityUnavailableError, match="default Codex"):
            await handlers.fork(ForkInput(lane=lane.ref, name="copy"), ctx)

        assert client.calls == []
        assert [stored.id for stored in await store.list_lanes()] == ["dsp_root"]
    finally:
        await store.close()
