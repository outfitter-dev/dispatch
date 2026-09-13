"""Exact provider routing, support, availability, and generation tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from structlog.testing import capture_logs

from outfitter.dispatch.client.errors import AppServerError, TransportError
from outfitter.dispatch.client.models import ThreadInfo
from outfitter.dispatch.config import RuntimePolicy
from outfitter.dispatch.contracts.errors import CapabilityUnavailableError
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import (
    AttachInput,
    DiscoverInput,
    ForkInput,
    ImageUrlContent,
    LaneSyncInput,
    LaneTextInput,
    NewInput,
    RosterInput,
    SearchInput,
    SendInput,
    ShowInput,
    ThreadTargetInput,
)
from outfitter.dispatch.core.providers import (
    CodexLaneAdapter,
    PreparedProviderRequest,
    ProviderAction,
    ProviderAvailability,
    ProviderBindingFacts,
    ProviderDurability,
    ProviderRouter,
    ProviderSubmissionAccepted,
    ProviderSubmissionRejected,
    ProviderSubmissionUnknown,
    ProviderTarget,
)
from outfitter.dispatch.core.turn_settings import TurnStartSettings
from outfitter.dispatch.registry.models import Lane
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID, Registry
from tests.fakes import FakeLaneClient, make_ctx


def _lane(**changes: object) -> Lane:
    values: dict[str, object] = {
        "id": "native-codex",
        "provider": "codex",
        "binding_id": DEFAULT_CODEX_BINDING_ID,
        "provider_thread_id": "native-codex",
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


@pytest.mark.parametrize(
    ("outcome", "expected_type"),
    [
        ("accepted", ProviderSubmissionAccepted),
        ("rejected", ProviderSubmissionRejected),
        ("unknown", ProviderSubmissionUnknown),
    ],
)
async def test_codex_adapter_classifies_prepared_submission_outcome(
    outcome: str,
    expected_type: type[
        ProviderSubmissionAccepted | ProviderSubmissionRejected | ProviderSubmissionUnknown
    ],
) -> None:
    class OutcomeClient(FakeLaneClient):
        async def turn_start(self, *args: object, **kwargs: object) -> dict[str, object]:
            self._record("turn_start", args=args, **kwargs)
            if outcome == "rejected":
                raise AppServerError(-32602, "invalid request")
            if outcome == "unknown":
                raise TransportError("lost acknowledgment")
            return {"submissionId": "submission-1", "turn": {"id": "turn-1"}}

    client = OutcomeClient()
    result = await CodexLaneAdapter(client).submit_prepared(
        PreparedProviderRequest(
            target=ProviderTarget(
                lane_id="native-codex",
                provider="codex",
                binding_id=DEFAULT_CODEX_BINDING_ID,
                native_session_id="native-codex",
            ),
            action=ProviderAction.SEND,
            transport="turn",
            correlation_id="receipt-1",
            text="hello",
            cwd="/tmp",
            settings=TurnStartSettings(),
        )
    )

    assert isinstance(result, expected_type)
    if isinstance(result, ProviderSubmissionAccepted):
        assert result.submission_id == "submission-1"
        assert result.turn_id == "turn-1"


def test_route_never_falls_back_for_missing_binding_or_native_identity() -> None:
    router = ProviderRouter.default_codex(FakeLaneClient())

    with pytest.raises(CapabilityUnavailableError, match="not registered"):
        router.route_lane(
            _lane(
                id="dsp_other",
                provider="hermes",
                binding_id="default",
                provider_thread_id="native-codex",
            ),
            ProviderAction.READ,
        )
    with pytest.raises(CapabilityUnavailableError, match="no provider thread identity"):
        router.route_lane(
            _lane(
                id="dsp_reserved", provider="hermes", binding_id="default", provider_thread_id=None
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


@pytest.mark.parametrize(
    ("source", "supported_action"),
    [
        ("own", ProviderAction.QUEUE_NATIVE),
        ("attached", ProviderAction.SEND),
    ],
)
async def test_queue_capability_depends_on_lane_ownership(
    source: Literal["own", "attached"], supported_action: ProviderAction
) -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(id="native-codex", handle="@lane", source=source)
        client = FakeLaneClient()
        ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
        ctx.providers = ProviderRouter(
            (CodexLaneAdapter(client, supported_actions=frozenset({supported_action})),)
        )

        view = await handlers.show(ShowInput(lane=lane.ref), ctx)

        assert view.capabilities.queue is False
    finally:
        await store.close()


@pytest.mark.parametrize(
    "inp",
    [
        NewInput(name="send-preflight", text="hello"),
        NewInput(name="goal-preflight", goal="ship it", send=False),
    ],
)
async def test_new_preflights_required_followup_actions_before_launch(inp: NewInput) -> None:
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter(
            (CodexLaneAdapter(client, supported_actions=frozenset({ProviderAction.LAUNCH})),)
        )

        with pytest.raises(CapabilityUnavailableError, match="unsupported"):
            await handlers.new_lane(inp, ctx)

        assert client.calls == []
        assert await store.list_lanes() == []
    finally:
        await store.close()


@pytest.mark.parametrize(
    "inp",
    [
        NewInput(name="send-preflight", text="hello"),
        NewInput(name="goal-preflight", goal="ship it", send=False),
    ],
)
async def test_new_dry_run_preflights_the_same_followup_actions_as_launch(
    inp: NewInput,
) -> None:
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter(
            (CodexLaneAdapter(client, supported_actions=frozenset({ProviderAction.LAUNCH})),)
        )

        with pytest.raises(CapabilityUnavailableError, match="unsupported"):
            await handlers.plan_new_lane(inp, ctx)

        assert client.calls == []
        assert await store.list_lanes() == []
    finally:
        await store.close()


async def test_new_treats_unsupported_optional_rename_as_best_effort(tmp_path: Path) -> None:
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter(
            (
                CodexLaneAdapter(
                    client,
                    supported_actions=frozenset(
                        {ProviderAction.LAUNCH, ProviderAction.CONFIG_READ}
                    ),
                ),
            )
        )

        result = await handlers.new_lane(
            NewInput(name="worker", send=False, cwd=str(tmp_path), workspace="none"), ctx
        )

        assert result.id == "lane-1"
        assert [name for name, _ in client.calls] == ["config_read", "thread_start"]
        assert (await store.get_lane("lane-1")).id == "lane-1"
    finally:
        await store.close()


async def test_attached_write_preparation_reuses_operation_route() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: FakeLaneClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset({ProviderAction.INJECT_CONTEXT}),
                    availability=ProviderAvailability(ready=True),
                    durability=ProviderDurability(),
                )

        client = FakeLaneClient()
        ctx = make_ctx(
            store,
            client,
            policy=RuntimePolicy(allow_attached_writes=True),
        )
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))
        lane = await store.add_lane(
            id="dsp_synthetic",
            handle="@synthetic",
            source="attached",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_thread_id="native-session",
        )

        result = await handlers.brief(
            LaneTextInput(lane=lane.ref, text="context"),
            ctx,
        )

        assert result.op == "brief"
        assert [name for name, _ in client.calls] == ["thread_resume", "inject_items"]
        assert all(call[1]["thread_id"] == "native-session" for call in client.calls)
    finally:
        await store.close()


async def test_nondefault_sync_is_not_advertised_and_fails_before_effects() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: FakeLaneClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset({ProviderAction.READ, ProviderAction.SYNC}),
                    availability=ProviderAvailability(ready=True),
                    durability=ProviderDurability(),
                )

        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))
        lane = await store.add_lane(
            id="dsp_synthetic",
            handle="@synthetic",
            source="own",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_thread_id="native-session",
        )

        assert (await handlers.show(ShowInput(lane=lane.ref), ctx)).capabilities.sync is False
        with pytest.raises(CapabilityUnavailableError, match="default Codex"):
            await handlers.sync_lane(LaneSyncInput(lane=lane.ref), ctx)

        assert client.calls == []
        assert (
            await store.get_provider_thread(
                "synthetic", "native-session", binding_id="synthetic-binding"
            )
            is None
        )
        assert await store.get_lane_sync(lane.id) is None
        assert not any(action.op == "sync" for action in await store.recent_actions())
    finally:
        await store.close()


async def test_attach_sync_on_nondefault_lane_fails_before_provider_io() -> None:
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        # A registered adapter that advertises SYNC for the non-default binding: without the
        # attach guard, route_lane would succeed and _sync_lane would issue thread/read.
        ctx.providers = ProviderRouter(
            (
                CodexLaneAdapter(
                    client,
                    binding_id="profile-a",
                    availability=ProviderAvailability(ready=True),
                ),
            )
        )
        lane = await store.add_lane(
            id="dsp_profile_a",
            handle="@profile-a",
            source="own",
            provider="codex",
            binding_id="profile-a",
            provider_thread_id="native-shared",
        )

        with pytest.raises(CapabilityUnavailableError, match="default Codex"):
            await handlers.attach_lane(AttachInput(thread=lane.id, sync=True), ctx)

        assert client.calls == []
        assert await store.get_lane_sync(lane.id) is None
        assert not any(action.op == "sync" for action in await store.recent_actions())
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
            provider_thread_id="native-session",
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
        lifecycle_actions = [
            action for action in await store.recent_actions() if action.op in {"archive", "restore"}
        ]
        assert {(action.op, action.lane) for action in lifecycle_actions} == {
            ("archive", "dsp_synthetic"),
            ("restore", "dsp_synthetic"),
        }
    finally:
        await store.close()


async def test_lane_search_maps_qualified_native_identity_to_stable_lane() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: FakeLaneClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset({ProviderAction.SEARCH}),
                    availability=ProviderAvailability(ready=True),
                    durability=ProviderDurability(),
                )

        client = FakeLaneClient()
        client.read_result = {
            "thread": {
                "id": "native-session",
                "turns": [
                    {
                        "id": "turn-1",
                        "items": [{"id": "item-1", "type": "agentMessage", "text": "needle"}],
                    }
                ],
            }
        }
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))
        lane = await store.add_lane(
            id="dsp_synthetic",
            handle="@synthetic",
            source="own",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_thread_id="native-session",
        )

        result = await handlers.search(SearchInput(query="needle", lane=lane.ref), ctx)

        assert [match.id for match in result.matches] == ["dsp_synthetic"]
        assert result.matches[0].ref == lane.ref
        assert result.matches[0].handle == "@synthetic"
        assert result.matches[0].managed is True
        assert [name for name, _ in client.calls] == ["thread_read"]
        assert client.calls[0][1]["thread_id"] == "native-session"
    finally:
        await store.close()


async def test_nondefault_rich_send_rejects_images_before_provider_io() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: FakeLaneClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset({ProviderAction.SEND}),
                    availability=ProviderAvailability(ready=True),
                    durability=ProviderDurability(),
                )

        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))
        lane = await store.add_lane(
            id="dsp_synthetic",
            handle="@synthetic",
            source="own",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_thread_id="native-session",
        )

        with pytest.raises(CapabilityUnavailableError, match="default Codex binding"):
            await handlers.send_message(
                SendInput(
                    lane=lane.ref,
                    content=[ImageUrlContent(url="https://example.com/image.png")],
                ),
                ctx,
            )

        assert client.calls == []
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
            provider_thread_id="native-root",
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


async def test_roster_uses_selected_binding_and_maps_native_ids_to_stable_lanes() -> None:
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
        client.list_result = [ThreadInfo(id="native-child", parent_thread_id="native-root")]
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter((SyntheticAdapter(client),))
        root = await store.add_lane(
            id="dsp_root",
            handle="@root",
            source="own",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_thread_id="native-root",
        )
        await store.add_lane(
            id="dsp_child",
            handle="@child",
            source="own",
            provider="synthetic",
            binding_id="synthetic-binding",
            provider_thread_id="native-child",
        )

        result = await handlers.roster(RosterInput(root=root.ref), ctx)

        assert {lane.id for lane in result.lanes} == {"dsp_root", "dsp_child"}
        assert [name for name, _ in client.calls] == ["thread_list"]
        _, call = client.calls[0]
        assert call["ancestor_thread_id"] == "native-root"
        assert await store.get_provider_thread(
            "synthetic", "native-child", binding_id="synthetic-binding"
        )
        assert await store.get_provider_thread("codex", "native-child") is None
    finally:
        await store.close()


async def test_filtered_discovery_rejects_nondefault_selector_before_effects() -> None:
    store = await Registry.open()
    try:

        class SyntheticAdapter(CodexLaneAdapter):
            def __init__(self, client: FakeLaneClient) -> None:
                super().__init__(client, binding_id="synthetic-binding")
                self.facts = ProviderBindingFacts(
                    provider="synthetic",
                    binding_id="synthetic-binding",
                    supported_actions=frozenset({ProviderAction.DISCOVER}),
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
            provider_thread_id="native-root",
        )

        with pytest.raises(CapabilityUnavailableError, match="default Codex"):
            await handlers.discover(DiscoverInput(root=lane.ref), ctx)

        assert client.calls == []
        assert (
            await store.get_provider_thread(
                "synthetic", "native-root", binding_id="synthetic-binding"
            )
            is None
        )
    finally:
        await store.close()


async def test_fork_treats_unsupported_optional_rename_as_best_effort() -> None:
    store = await Registry.open()
    try:
        client = FakeLaneClient()
        ctx = make_ctx(store, client)
        ctx.providers = ProviderRouter(
            (
                CodexLaneAdapter(
                    client,
                    supported_actions=frozenset({ProviderAction.FORK, ProviderAction.CONFIG_READ}),
                ),
            )
        )
        source = await store.add_lane(id="native-codex", handle="@root", source="own")

        with capture_logs() as logs:
            result = await handlers.fork(ForkInput(lane=source.ref, name="copy"), ctx)

        assert result.id == "native-codex-fork"
        assert (await store.get_lane("native-codex-fork")).handle == "@copy"
        assert "thread_set_name" not in [name for name, _ in client.calls]
        assert [entry["event"] for entry in logs if entry["log_level"] == "warning"] == [
            "lane.name_set_failed"
        ]
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
            provider_thread_id="native-root",
        )

        detail = await handlers.show(ShowInput(lane=lane.ref), ctx)
        assert detail.capabilities.fork is False
        with pytest.raises(CapabilityUnavailableError, match="default Codex"):
            await handlers.fork(ForkInput(lane=lane.ref, name="copy"), ctx)

        assert client.calls == []
        assert [stored.id for stored in await store.list_lanes()] == ["dsp_root"]
    finally:
        await store.close()
