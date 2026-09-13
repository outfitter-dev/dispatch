"""Public compatibility checks for binding-scoped managed identities."""

from __future__ import annotations

import pytest

from outfitter.dispatch.contracts.errors import (
    CapabilityUnavailableError,
    NotFoundError,
    ValidationError,
)
from outfitter.dispatch.core import handlers, queue
from outfitter.dispatch.core.models import (
    AttachInput,
    DiscoverInput,
    GoalGetInput,
    HistoryInput,
    LaneRenameInput,
    LaneSyncInput,
    LaneTextInput,
    RosterInput,
    SearchInput,
    ShowInput,
    ThreadTargetInput,
    TranscriptInput,
    WatchInput,
)
from outfitter.dispatch.core.selectors import resolve_managed_selector
from outfitter.dispatch.core.topology import lane_topology_views
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID, Registry
from tests.fakes import FakeLaneClient, make_ctx
from tests.fixtures.registry.builders import provider_thread_observation


async def test_default_codex_and_non_codex_outputs_preserve_public_aliases() -> None:
    store = await Registry.open()
    try:
        codex = await store.add_lane(
            id="codex-native", handle="@codex", source="own", status="idle"
        )
        other = await store.add_lane(
            id="dsp_other",
            handle="@other",
            source="own",
            status="idle",
            provider="claude",
            binding_id="profile-a",
            provider_thread_id="claude-native",
        )
        client = FakeLaneClient()
        ctx = make_ctx(store, client)

        roster = await handlers.roster(RosterInput(), ctx)
        by_id = {lane.id: lane for lane in roster.lanes}
        codex_view = by_id[codex.id]
        assert codex_view.provider == "codex"
        assert codex_view.binding_id == DEFAULT_CODEX_BINDING_ID
        assert codex_view.provider_thread_id == codex.id
        assert codex_view.ref == codex.ref

        other_view = by_id[other.id]
        assert other_view.provider == "claude"
        assert other_view.binding_id == "profile-a"
        assert other_view.provider_thread_id == "claude-native"
        assert other_view.id == other.id
        assert other_view.capabilities.send is False
        assert other_view.capabilities.read is False
        assert other_view.capabilities.sync is False
        assert other_view.capabilities.tail is False
        assert other_view.writable is False

        overview = await handlers.history(HistoryInput(), ctx)
        other_summary = next(summary for summary in overview.threads if summary.id == other.id)
        assert other_summary.provider == "claude"
        assert other_summary.binding_id == "profile-a"
        assert other_summary.provider_thread_id == "claude-native"

        with pytest.raises(NotFoundError, match="no managed thread"):
            await resolve_managed_selector(ctx, "claude-native")
        with pytest.raises(CapabilityUnavailableError, match="execution is not supported"):
            await handlers.send(LaneTextInput(lane=other.ref, text="blocked"), ctx)
        assert not client.calls
    finally:
        await store.close()


async def test_non_default_binding_reads_fail_before_codex_client_calls() -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(
            id="dsp_other",
            handle="@other",
            source="own",
            status="idle",
            provider="codex",
            binding_id="profile-a",
            provider_thread_id="native-shared",
        )
        client = FakeLaneClient()
        ctx = make_ctx(store, client)

        detail = await handlers.show(ShowInput(lane=lane.ref), ctx)
        assert detail.capabilities.read is False
        assert detail.topology.observed is False
        assert not client.calls

        calls = (
            handlers.attach_lane(AttachInput(thread=lane.id, sync=True), ctx),
            handlers.show(ShowInput(lane=lane.ref, include_transcript=True), ctx),
            handlers.show(ShowInput(lane=lane.ref, topology=True), ctx),
            handlers.sync_lane(LaneSyncInput(lane=lane.ref), ctx),
            handlers.watch(WatchInput(lane=lane.ref, timeout=0), ctx),
            handlers.transcript(TranscriptInput(lane=lane.ref), ctx),
            handlers.history(HistoryInput(lane=lane.ref, view="summary"), ctx),
            handlers.goal_get(GoalGetInput(lane=lane.ref), ctx),
            handlers.rename_lane(LaneRenameInput(old=lane.ref, new="renamed"), ctx),
            handlers.search(SearchInput(query="needle", lane=lane.ref), ctx),
            handlers.roster(RosterInput(parent=lane.ref), ctx),
            handlers.discover(DiscoverInput(parent=lane.ref), ctx),
            handlers.archive(ThreadTargetInput(target=lane.ref), ctx),
            handlers.restore(ThreadTargetInput(target=lane.ref), ctx),
        )
        for call in calls:
            with pytest.raises(CapabilityUnavailableError):
                await call
            assert not client.calls

        with pytest.raises(ValidationError, match="current Codex thread"):
            await handlers._resolve_self(ctx, lane.id)
        queued = await store.enqueue_message(lane=lane.id, text="must not send")
        assert await queue.drain_next_queued_message(ctx, lane.id) is False
        assert (await store.get_queued_message(queued.id)).status == "pending"
        assert not client.calls
    finally:
        await store.close()


async def test_malformed_default_codex_identity_cannot_execute() -> None:
    store = await Registry.open()
    try:
        lane = await store.add_lane(id="codex-stable", handle="@codex", source="own", status="idle")
        await store._conn.execute(
            "UPDATE lanes SET provider_thread_id = 'wrong-native' WHERE id = ?", (lane.id,)
        )
        await store._conn.commit()
        client = FakeLaneClient()
        ctx = make_ctx(store, client)

        with pytest.raises(CapabilityUnavailableError):
            await handlers.transcript(TranscriptInput(lane=lane.ref), ctx)
        assert not client.calls
    finally:
        await store.close()


async def test_lane_topology_shares_one_node_budget_across_bindings() -> None:
    store = await Registry.open()
    try:
        lanes = []
        for provider, binding_id in (("codex", DEFAULT_CODEX_BINDING_ID), ("claude", "profile-a")):
            for index in range(3):
                native_id = f"{binding_id}-thread-{index}"
                await store.upsert_provider_thread(
                    provider_thread_observation(
                        provider=provider, binding_id=binding_id, provider_thread_id=native_id
                    )
                )
                lanes.append(
                    await store.add_lane(
                        id=native_id if provider == "codex" else f"dsp_{binding_id}_{index}",
                        handle=f"@{binding_id}-{index}",
                        source="own",
                        status="idle",
                        provider=provider,
                        binding_id=binding_id,
                        provider_thread_id=native_id,
                    )
                )
        foreign = [lane.id for lane in lanes if lane.binding_id == "profile-a"]

        views = await lane_topology_views(store, lanes, max_nodes=4)
        assert sum(view.observed for view in views.values()) == 4
        assert all(views[lane_id].truncated for lane_id in foreign)

        exhausted = await lane_topology_views(store, lanes, max_nodes=3)
        assert sum(view.observed for view in exhausted.values()) == 3
        assert all(not exhausted[lane_id].observed for lane_id in foreign)
        assert all(exhausted[lane_id].truncated for lane_id in foreign)
    finally:
        await store.close()
