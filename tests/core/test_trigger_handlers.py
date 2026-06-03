"""Trigger management op handlers (add/list/rm/pause/resume)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from outfitter.dispatch.contracts.errors import NotFoundError, ValidationError
from outfitter.dispatch.core import trigger_handlers
from outfitter.dispatch.core.models import (
    TriggerAddInput,
    TriggerIdInput,
    TriggerListInput,
)
from outfitter.dispatch.registry.store import Registry
from tests.fakes import make_ctx


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open()
    try:
        yield s
    finally:
        await s.close()


async def test_add_interval_and_list(store: Registry) -> None:
    ctx = make_ctx(store)
    view = await trigger_handlers.trigger_add(
        TriggerAddInput(name="p", lane="@x", when="interval", action="send", text="hi", seconds=60),
        ctx,
    )
    assert view.when == "interval"
    assert view.action == "send"
    listed = await trigger_handlers.trigger_list(TriggerListInput(), ctx)
    assert [t.name for t in listed.triggers] == ["p"]


async def test_add_event_trigger_labels_event(store: Registry) -> None:
    ctx = make_ctx(store)
    view = await trigger_handlers.trigger_add(
        TriggerAddInput(name="c", lane="@x", when="turn_completed", action="brief", text="done"),
        ctx,
    )
    assert view.when == "turn_completed"


async def test_interval_without_seconds_is_validation_error(store: Registry) -> None:
    ctx = make_ctx(store)
    with pytest.raises(ValidationError):
        await trigger_handlers.trigger_add(
            TriggerAddInput(name="p", lane="@x", when="interval", action="send", text="hi"), ctx
        )


async def test_pause_then_resume(store: Registry) -> None:
    ctx = make_ctx(store)
    added = await trigger_handlers.trigger_add(
        TriggerAddInput(name="p", lane="@x", when="interval", action="send", text="hi", seconds=60),
        ctx,
    )
    paused = await trigger_handlers.trigger_pause(TriggerIdInput(id=added.id), ctx)
    assert paused.enabled is False
    assert (await store.get_trigger(added.id)).enabled is False
    resumed = await trigger_handlers.trigger_resume(TriggerIdInput(id=added.id), ctx)
    assert resumed.enabled is True


async def test_remove_missing_raises(store: Registry) -> None:
    ctx = make_ctx(store)
    with pytest.raises(NotFoundError):
        await trigger_handlers.trigger_rm(TriggerIdInput(id="nope"), ctx)
