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
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID, Registry
from tests.fakes import FakeLaneClient, make_ctx


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
            provider_session_id="claude-native",
        )
        client = FakeLaneClient()
        ctx = make_ctx(store, client)

        roster = await handlers.roster(RosterInput(), ctx)
        by_id = {lane.id: lane for lane in roster.lanes}
        codex_view = by_id[codex.id]
        assert codex_view.provider == "codex"
        assert codex_view.binding_id == DEFAULT_CODEX_BINDING_ID
        assert codex_view.provider_session_id == codex.id
        assert codex_view.ref == codex.ref

        other_view = by_id[other.id]
        assert other_view.provider == "claude"
        assert other_view.binding_id == "profile-a"
        assert other_view.provider_session_id == "claude-native"
        assert other_view.id == other.id
        assert other_view.capabilities.send is False
        assert other_view.capabilities.read is False
        assert other_view.capabilities.sync is False
        assert other_view.capabilities.tail is False
        assert other_view.writable is False

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
            provider_session_id="native-shared",
        )
        client = FakeLaneClient()
        ctx = make_ctx(store, client)

        detail = await handlers.show(ShowInput(lane=lane.ref), ctx)
        assert detail.capabilities.read is False
        assert detail.topology.observed is False
        assert not client.calls

        calls = (
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
            "UPDATE lanes SET provider_session_id = 'wrong-native' WHERE id = ?", (lane.id,)
        )
        await store._conn.commit()
        client = FakeLaneClient()
        ctx = make_ctx(store, client)

        with pytest.raises(CapabilityUnavailableError):
            await handlers.transcript(TranscriptInput(lane=lane.ref), ctx)
        assert not client.calls
    finally:
        await store.close()
