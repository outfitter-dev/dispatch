"""Selector resolver tests for refs, full ids, labels, and ambiguity."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import NotFoundError, ValidationError
from outfitter.dispatch.core.selectors import resolve_managed_selector, resolve_thread_selector
from outfitter.dispatch.registry.models import LaneSync
from outfitter.dispatch.registry.store import Registry
from tests.fakes import make_ctx


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open()
    try:
        yield s
    finally:
        await s.close()


async def test_resolver_accepts_ref_full_id_handle_and_title(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@docs", source="own", cwd="/work")
    await store.upsert_lane_sync(
        LaneSync(lane=lane.id, state="metadata", display_name="Docs Thread")
    )
    ctx = make_ctx(store)

    assert (await resolve_managed_selector(ctx, lane.ref)).thread_id == "thread-1"
    assert (await resolve_managed_selector(ctx, "thread-1")).kind == "thread_id"
    assert (await resolve_managed_selector(ctx, "@docs")).kind == "handle"
    assert (await resolve_managed_selector(ctx, "Docs Thread")).kind == "title"


async def test_resolver_accepts_current_title_without_handle_prefix(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@Docs Thread", source="own")

    resolved = await resolve_managed_selector(make_ctx(store), "Docs Thread")

    assert resolved.thread_id == lane.id
    assert resolved.kind == "title"


async def test_resolver_rejects_ambiguous_title_with_candidates(store: Registry) -> None:
    one = await store.add_lane(id="thread-1", handle="@one", source="own", cwd="/a")
    two = await store.add_lane(id="thread-2", handle="@two", source="attached", cwd="/b")
    await store.upsert_lane_sync(LaneSync(lane=one.id, state="metadata", display_name="Same"))
    await store.upsert_lane_sync(LaneSync(lane=two.id, state="metadata", display_name="Same"))

    with pytest.raises(ValidationError) as exc:
        await resolve_managed_selector(make_ctx(store), "Same")

    message = str(exc.value)
    assert "ambiguous selector" in message
    assert one.ref in message
    assert two.ref in message


async def test_fuzzy_resolution_is_read_only_opt_in(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@release-notes", source="own")
    ctx = make_ctx(store)

    with pytest.raises(NotFoundError):
        await resolve_managed_selector(ctx, "release", allow_fuzzy=False)

    resolved = await resolve_managed_selector(ctx, "release", allow_fuzzy=True)
    assert resolved.thread_id == lane.id
    assert resolved.kind == "fuzzy_title"


async def test_thread_resolver_can_return_raw_unmanaged_id(store: Registry) -> None:
    resolved = await resolve_thread_selector(
        make_ctx(store), "019e9598-9214-7ed1-ac40-52d6d675d3e7", allow_unmanaged_raw=True
    )

    assert resolved.managed is False
    assert resolved.thread_id == "019e9598-9214-7ed1-ac40-52d6d675d3e7"
