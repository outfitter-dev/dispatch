from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
import pytest_asyncio

from outfitter.dispatch.client.errors import AppServerError, ProtocolError, TransportError
from outfitter.dispatch.client.models import (
    ThreadInfo,
    ThreadItemsPage,
    ThreadResumeInitialTurnsPageParams,
    ThreadResumeResult,
    ThreadTurn,
    ThreadTurnsPage,
)
from outfitter.dispatch.core.backfill import backfill_codex_history
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Registry]:
    registry = await Registry.open(tmp_path / "registry.db")
    yield registry
    await registry.close()


class _BackfillClient(FakeLaneClient):
    def __init__(
        self,
        *,
        initial_page: ThreadTurnsPage | None = None,
        turn_pages: dict[str | None, ThreadTurnsPage] | None = None,
        recent_turn_pages: dict[str | None, ThreadTurnsPage] | None = None,
        full_turn_pages: dict[str | None, ThreadTurnsPage] | None = None,
        item_pages: dict[tuple[str, str | None], ThreadItemsPage] | None = None,
        item_errors: dict[tuple[str, str | None], BaseException] | None = None,
        resume_error: BaseException | None = None,
        turn_error: BaseException | None = None,
        item_error: BaseException | None = None,
        metadata_resume_error: BaseException | None = None,
    ) -> None:
        super().__init__()
        self.initial_page = initial_page or ThreadTurnsPage()
        self.turn_pages = turn_pages or {}
        self.recent_turn_pages = recent_turn_pages or {}
        self.full_turn_pages = full_turn_pages or {}
        self.item_pages = item_pages or {}
        self.item_errors = item_errors or {}
        self.resume_error = resume_error
        self.turn_error = turn_error
        self.item_error = item_error
        self.metadata_resume_error = metadata_resume_error

    async def thread_resume(self, thread_id: str, **kwargs: object) -> ThreadInfo:
        self._record("thread_resume", thread_id=thread_id, **kwargs)
        if self.metadata_resume_error is not None:
            raise self.metadata_resume_error
        return ThreadInfo(id=thread_id)

    async def thread_resume_full(self, thread_id: str, **kwargs: object) -> ThreadResumeResult:
        self._record("thread_resume_full", thread_id=thread_id, **kwargs)
        if self.resume_error is not None:
            raise self.resume_error
        return ThreadResumeResult(
            thread=ThreadInfo(id=thread_id),
            initial_turns_page=self.initial_page,
        )

    async def thread_turns_list(self, thread_id: str, **kwargs: object) -> ThreadTurnsPage:
        self._record("thread_turns_list", thread_id=thread_id, **kwargs)
        if self.turn_error is not None:
            raise self.turn_error
        cursor = kwargs.get("cursor")
        assert cursor is None or isinstance(cursor, str)
        if kwargs.get("items_view") == "full":
            return self.full_turn_pages[cursor]
        if kwargs.get("sort_direction") == "asc":
            return self.recent_turn_pages[cursor]
        return self.turn_pages[cursor]

    async def thread_items_list(self, thread_id: str, **kwargs: object) -> ThreadItemsPage:
        self._record("thread_items_list", thread_id=thread_id, **kwargs)
        if self.item_error is not None:
            raise self.item_error
        turn_id = kwargs.get("turn_id")
        cursor = kwargs.get("cursor")
        assert isinstance(turn_id, str)
        assert cursor is None or isinstance(cursor, str)
        if error := self.item_errors.get((turn_id, cursor)):
            raise error
        return self.item_pages[(turn_id, cursor)]


def _turn(turn_id: str) -> ThreadTurn:
    return ThreadTurn(id=turn_id, status="completed", items_view="notLoaded")


def _item(item_id: str) -> dict[str, object]:
    return {"id": item_id, "type": "agentMessage", "text": item_id}


@pytest.mark.asyncio
async def test_paged_resume_capability_failure_uses_metadata_observation_fallback(
    store: Registry,
) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(resume_error=AppServerError(-32602, "unsupported"))
    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert result.capability == "unsupported"
    assert result.observation_enabled is True
    assert [call[0] for call in client.calls] == ["thread_resume_full", "thread_resume"]


@pytest.mark.asyncio
async def test_resume_capability_failure_without_metadata_fallback_is_unobserved(
    store: Registry,
) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    result = await backfill_codex_history(
        client=_BackfillClient(
            resume_error=AppServerError(-32602, "unsupported"),
            metadata_resume_error=AppServerError(-32601, "unsupported"),
        ),
        registry=store,
        lane=lane,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert result.capability == "unsupported"
    assert result.observation_enabled is False


@pytest.mark.asyncio
async def test_first_backfill_requests_one_not_loaded_turn(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(
            data=[_turn("turn-2")],
            next_cursor="older-1",
            backwards_cursor="newer-1",
        )
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        max_turns=1,
        max_items=10,
        max_seconds=5,
    )

    call = client.calls[0]
    initial = call[1]["initial_turns_page"]
    assert isinstance(initial, ThreadResumeInitialTurnsPageParams)
    assert call[1]["exclude_turns"] is True
    assert initial.limit == 1
    assert initial.sort_direction == "desc"
    assert initial.items_view == "notLoaded"
    assert result.cursor == "older-1"
    assert result.item_turn_id == "turn-2"
    assert result.item_turn_cursor == "newer-1"
    assert result.item_turn_direction == "asc"
    assert result.item_cursor is None
    assert result.backwards_cursor is None
    assert result.pending_backwards_cursor == "newer-1"
    assert result.turns_indexed == 1
    assert result.items_indexed == 0
    assert result.truncated is True


@pytest.mark.asyncio
async def test_item_budget_continues_without_loss_and_indexes_exact_max(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-1")]),
        item_pages={
            ("turn-1", None): ThreadItemsPage(
                data=[_item("item-1"), _item("item-2")], next_cursor="items-2"
            ),
            ("turn-1", "items-2"): ThreadItemsPage(data=[_item("item-3")]),
        },
    )

    first = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        max_turns=10,
        max_items=2,
        max_seconds=5,
    )

    assert first.items_indexed == 2
    assert first.item_turn_id == "turn-1"
    assert first.item_cursor == "items-2"
    assert first.cursor is None
    assert first.complete is False
    assert first.truncated is True
    assert {item.item_id for item in await store.list_thread_items(lane=lane.id)} == {
        "item-1",
        "item-2",
    }

    second = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor=first.cursor,
        item_turn_id=first.item_turn_id,
        item_turn_cursor=first.item_turn_cursor,
        item_turn_direction=first.item_turn_direction,
        item_cursor=first.item_cursor,
        cursor_guard=first.cursor_guard,
        max_turns=10,
        max_items=2,
        max_seconds=5,
    )

    assert second.complete is True
    assert second.items_indexed == 1
    assert second.item_turn_id is None
    assert second.item_cursor is None
    assert {item.item_id for item in await store.list_thread_items(lane=lane.id)} == {
        "item-1",
        "item-2",
        "item-3",
    }


@pytest.mark.asyncio
async def test_pending_items_resume_before_next_turn_cursor(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        item_pages={
            ("turn-1", "items-2"): ThreadItemsPage(data=[_item("item-3")], next_cursor="items-3")
        }
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor="older-turn",
        item_turn_id="turn-1",
        item_turn_cursor="turn-fetch",
        item_cursor="items-2",
        max_turns=10,
        max_items=1,
        max_seconds=5,
    )

    assert [call[0] for call in client.calls] == ["thread_resume_full", "thread_items_list"]
    assert result.cursor == "older-turn"
    assert result.item_turn_id == "turn-1"
    assert result.item_turn_cursor == "turn-fetch"
    assert result.item_cursor == "items-3"
    assert result.items_indexed == 1


@pytest.mark.asyncio
async def test_turn_cursor_cycles_are_rejected(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        turn_pages={
            "A": ThreadTurnsPage(data=[_turn("turn-a")], next_cursor="B"),
            "B": ThreadTurnsPage(data=[_turn("turn-b")], next_cursor="A"),
        },
        item_pages={
            ("turn-a", None): ThreadItemsPage(),
            ("turn-b", None): ThreadItemsPage(),
        },
    )

    with pytest.raises(ProtocolError, match="repeated pagination cursor 'A'"):
        await backfill_codex_history(
            client=client,
            registry=store,
            lane=lane,
            cursor="A",
            max_turns=10,
            max_items=10,
            max_seconds=5,
        )


@pytest.mark.asyncio
async def test_item_cursor_cycles_are_rejected(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        item_pages={
            ("turn-1", "A"): ThreadItemsPage(next_cursor="B"),
            ("turn-1", "B"): ThreadItemsPage(next_cursor="A"),
        }
    )

    with pytest.raises(ProtocolError, match="repeated pagination cursor 'A'"):
        await backfill_codex_history(
            client=client,
            registry=store,
            lane=lane,
            item_turn_id="turn-1",
            item_cursor="A",
            max_turns=10,
            max_items=10,
            max_seconds=5,
        )


@pytest.mark.asyncio
async def test_turn_cursor_cycles_are_rejected_across_calls(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        turn_pages={
            "A": ThreadTurnsPage(data=[_turn("turn-a")], next_cursor="B"),
            "B": ThreadTurnsPage(data=[_turn("turn-b")], next_cursor="A"),
        }
    )

    first = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor="A",
        max_turns=1,
        max_items=10,
        max_seconds=5,
    )

    with pytest.raises(ProtocolError, match="repeated pagination cursor 'A'"):
        await backfill_codex_history(
            client=client,
            registry=store,
            lane=lane,
            cursor=first.cursor,
            item_turn_id=None,
            cursor_guard=first.cursor_guard,
            max_turns=10,
            max_items=10,
            max_seconds=5,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [-32601, -32602])
async def test_turn_list_capability_errors_are_unsupported(store: Registry, code: int) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    error = AppServerError(code, "unsupported")
    client = _BackfillClient(turn_error=error)

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor="turn-cursor",
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert result.capability == "unsupported"
    assert result.source == "metadata-jsonl-fallback"
    assert result.observation_enabled is True
    assert result.cursor == "turn-cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [-32601, -32602])
async def test_oversized_full_turn_fallback_stays_pending_until_budget_increases(
    store: Registry, code: int
) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    full_turn = ThreadTurn(
        id="turn-1",
        status="completed",
        items=[_item("item-1"), _item("item-2"), _item("item-3")],
    )
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-1")], next_cursor="older"),
        full_turn_pages={None: ThreadTurnsPage(data=[full_turn], next_cursor="older")},
        turn_pages={"older": ThreadTurnsPage()},
        item_error=AppServerError(code, "thread/items/list is not supported yet"),
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        max_turns=10,
        max_items=2,
        max_seconds=5,
    )

    assert result.capability == "turn-page-fallback"
    assert result.source == "codex-app-server:thread-turns-full"
    assert result.cursor == "older"
    assert result.item_turn_id == "turn-1"
    assert result.item_turn_cursor is None
    assert result.items_indexed == 0
    assert result.truncated is True
    assert await store.list_thread_items(lane=lane.id) == []
    full_call = client.calls[-1]
    assert full_call[0] == "thread_turns_list"
    assert full_call[1]["cursor"] is None
    assert full_call[1]["items_view"] == "full"

    resumed = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor=result.cursor,
        item_turn_id=result.item_turn_id,
        item_turn_cursor=result.item_turn_cursor,
        item_turn_direction=result.item_turn_direction,
        item_cursor=result.item_cursor,
        cursor_guard=result.cursor_guard,
        backwards_cursor=result.backwards_cursor,
        recent_cursor=result.recent_cursor,
        pending_backwards_cursor=result.pending_backwards_cursor,
        max_turns=10,
        max_items=4,
        max_seconds=5,
    )

    assert resumed.complete is True
    assert resumed.items_indexed == 3
    assert {item.item_id for item in await store.list_thread_items(lane=lane.id)} == {
        "item-1",
        "item-2",
        "item-3",
    }


@pytest.mark.asyncio
async def test_persisted_pending_turn_falls_back_using_saved_turn_cursor(
    store: Registry,
) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    full_turn = ThreadTurn(
        id="turn-1",
        status="completed",
        items=[_item("item-1"), _item("item-2")],
    )
    client = _BackfillClient(
        full_turn_pages={"turn-fetch": ThreadTurnsPage(data=[full_turn], next_cursor="older")},
        item_error=AppServerError(-32601, "thread/items/list is not supported yet"),
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor="older",
        item_turn_id="turn-1",
        item_turn_cursor="turn-fetch",
        max_turns=10,
        max_items=1,
        max_seconds=5,
    )

    assert result.capability == "turn-page-fallback"
    assert result.cursor == "older"
    assert result.items_indexed == 0
    assert result.truncated is True
    assert client.calls[-1][1]["cursor"] == "turn-fetch"
    assert await store.list_thread_items(lane=lane.id) == []


@pytest.mark.asyncio
async def test_partial_item_page_fallback_preserves_all_items_and_next_turn_cursor(
    store: Registry,
) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    full_turn = ThreadTurn(
        id="turn-2",
        status="completed",
        items=[_item("item-1"), _item("item-2")],
    )
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-2")], next_cursor="older-1"),
        full_turn_pages={None: ThreadTurnsPage(data=[full_turn], next_cursor="older-1")},
        turn_pages={"older-1": ThreadTurnsPage()},
        item_pages={
            ("turn-2", None): ThreadItemsPage(data=[_item("item-1")], next_cursor="items-2")
        },
        item_errors={
            ("turn-2", "items-2"): AppServerError(-32601, "thread/items/list is not supported yet")
        },
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert result.capability == "turn-page-fallback"
    assert result.cursor is None
    assert result.complete is True
    assert result.item_turn_id is None
    assert result.item_turn_cursor is None
    assert result.item_cursor is None
    assert {item.item_id for item in await store.list_thread_items(lane=lane.id)} == {
        "item-1",
        "item-2",
    }
    assert [(call[0], call[1].get("cursor")) for call in client.calls[1:]] == [
        ("thread_items_list", None),
        ("thread_items_list", "items-2"),
        ("thread_turns_list", None),
        ("thread_turns_list", "older-1"),
    ]


@pytest.mark.asyncio
async def test_full_turn_fallback_rejects_mismatched_turn_id(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        full_turn_pages={
            "turn-fetch": ThreadTurnsPage(
                data=[ThreadTurn(id="wrong-turn", status="completed", items=[_item("lost")])]
            )
        },
        item_error=AppServerError(-32601, "thread/items/list is not supported yet"),
    )

    with pytest.raises(ProtocolError, match="returned 'wrong-turn', expected 'turn-1'"):
        await backfill_codex_history(
            client=client,
            registry=store,
            lane=lane,
            cursor="older",
            item_turn_id="turn-1",
            item_turn_cursor="turn-fetch",
            max_turns=10,
            max_items=10,
            max_seconds=5,
        )

    assert await store.list_thread_items(lane=lane.id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AppServerError(-32000, "provider failed"),
        TransportError("connection closed"),
        ProtocolError("malformed response"),
    ],
)
async def test_non_capability_list_failures_surface(store: Registry, error: BaseException) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")

    with pytest.raises(type(error), match=str(error)):
        await backfill_codex_history(
            client=_BackfillClient(turn_error=error),
            registry=store,
            lane=lane,
            cursor="turn-cursor",
            max_turns=10,
            max_items=10,
            max_seconds=5,
        )


@pytest.mark.asyncio
async def test_max_bytes_stops_before_persisting_oversized_turn_page(
    store: Registry,
) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-1")]),
        item_pages={("turn-1", None): ThreadItemsPage()},
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        max_turns=10,
        max_items=10,
        max_seconds=5,
        max_bytes=1,
    )

    assert result.bytes_scanned > 1
    assert result.turns_indexed == 0
    assert result.item_turn_id is None
    assert result.truncated is True
    assert await store.list_thread_turns(lane=lane.id) == []

    resumed = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor=result.cursor,
        backwards_cursor=result.backwards_cursor,
        recent_cursor=result.recent_cursor,
        pending_backwards_cursor=result.pending_backwards_cursor,
        cursor_guard=result.cursor_guard,
        max_turns=10,
        max_items=10,
        max_seconds=5,
        max_bytes=10_000,
    )

    assert resumed.complete is True
    assert {turn.turn_id for turn in await store.list_thread_turns(lane=lane.id)} == {"turn-1"}


@pytest.mark.asyncio
async def test_item_page_over_byte_budget_is_not_persisted(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        item_pages={
            ("turn-1", None): ThreadItemsPage(data=[_item("item-1")]),
        }
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        item_turn_id="turn-1",
        max_turns=10,
        max_items=10,
        max_seconds=5,
        max_bytes=1,
    )

    assert result.bytes_scanned > 1
    assert result.items_indexed == 0
    assert result.item_turn_id == "turn-1"
    assert result.truncated is True
    assert await store.list_thread_items(lane=lane.id) == []


@pytest.mark.asyncio
async def test_provider_call_is_cancelled_at_max_seconds(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")

    class SlowClient(_BackfillClient):
        async def thread_resume_full(self, thread_id: str, **kwargs: object) -> ThreadResumeResult:
            await asyncio.sleep(0.1)
            return await super().thread_resume_full(thread_id, **kwargs)

    with pytest.raises(ProtocolError, match="exceeded max_seconds"):
        await backfill_codex_history(
            client=SlowClient(),
            registry=store,
            lane=lane,
            max_turns=10,
            max_items=10,
            max_seconds=0.001,
        )


@pytest.mark.asyncio
async def test_pending_fallback_capability_survives_an_immediate_budget_stop(
    store: Registry,
) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")

    result = await backfill_codex_history(
        client=_BackfillClient(),
        registry=store,
        lane=lane,
        item_turn_id="turn-1",
        history_capability="turn-page-fallback",
        max_turns=10,
        max_items=10,
        max_seconds=5,
        max_bytes=0,
    )

    assert result.capability == "turn-page-fallback"
    assert result.item_turn_id == "turn-1"
    assert result.truncated is True


@pytest.mark.asyncio
async def test_max_seconds_stops_between_pages_with_pending_state(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    clock: Callable[[], float] = iter((0.0, 0.0, 0.0, 2.0)).__next__
    client = _BackfillClient(initial_page=ThreadTurnsPage(data=[_turn("turn-1")]))

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        max_turns=10,
        max_items=10,
        max_seconds=1,
        monotonic=clock,
    )

    assert result.item_turn_id == "turn-1"
    assert result.items_indexed == 0
    assert result.truncated is True


@pytest.mark.asyncio
async def test_full_turn_fallback_respects_remaining_byte_budget(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    full_turn = ThreadTurn(
        id="turn-1",
        status="completed",
        items=[_item("item-1")],
    )
    client = _BackfillClient(
        full_turn_pages={"turn-fetch": ThreadTurnsPage(data=[full_turn])},
        item_error=AppServerError(-32601, "unsupported"),
    )

    first = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        item_turn_id="turn-1",
        item_turn_cursor="turn-fetch",
        max_turns=10,
        max_items=10,
        max_bytes=1,
        max_seconds=5,
    )

    assert first.bytes_scanned > 1
    assert first.item_turn_id == "turn-1"
    assert first.item_turn_cursor == "turn-fetch"
    assert first.items_indexed == 0
    assert first.truncated is True
    assert await store.list_thread_items(lane=lane.id) == []

    second = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        item_turn_id=first.item_turn_id,
        item_turn_cursor=first.item_turn_cursor,
        item_turn_direction=first.item_turn_direction,
        cursor_guard=first.cursor_guard,
        max_turns=10,
        max_items=10,
        max_bytes=10_000,
        max_seconds=5,
    )

    assert second.complete is True
    assert second.items_indexed == 1
    assert [item.item_id for item in await store.list_thread_items(lane=lane.id)] == ["item-1"]


@pytest.mark.asyncio
async def test_first_page_fallback_finds_pending_turn_after_newer_turn_arrives(
    store: Registry,
) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    first = await backfill_codex_history(
        client=_BackfillClient(
            initial_page=ThreadTurnsPage(data=[_turn("old")], next_cursor="older")
        ),
        registry=store,
        lane=lane,
        max_turns=1,
        max_items=10,
        max_seconds=5,
    )
    old = ThreadTurn(
        id="old",
        status="completed",
        items=[_item("old-item")],
    )
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("new")], next_cursor="old-page"),
        full_turn_pages={
            None: ThreadTurnsPage(
                data=[ThreadTurn(id="new", status="completed", items=[])],
                next_cursor="old-page",
            ),
            "old-page": ThreadTurnsPage(data=[old], backwards_cursor="old-anchor"),
        },
        turn_pages={"older": ThreadTurnsPage()},
        item_error=AppServerError(-32601, "unsupported"),
    )

    resumed = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor=first.cursor,
        item_turn_id=first.item_turn_id,
        item_turn_cursor=first.item_turn_cursor,
        item_turn_direction=first.item_turn_direction,
        item_cursor=first.item_cursor,
        cursor_guard=first.cursor_guard,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert resumed.items_indexed == 1
    assert [call[1].get("cursor") for call in client.calls if call[0] == "thread_turns_list"][
        :2
    ] == [None, "old-page"]
    assert [item.item_id for item in await store.list_thread_items(lane=lane.id)] == ["old-item"]


@pytest.mark.asyncio
async def test_recent_full_turn_fallback_uses_ascending_anchor(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    full_turn = ThreadTurn(
        id="turn-2",
        status="completed",
        items=[_item("item-2")],
    )
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-2")], backwards_cursor="back-2"),
        full_turn_pages={"recent-2": ThreadTurnsPage(data=[full_turn])},
        item_error=AppServerError(-32601, "unsupported"),
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        item_turn_id="turn-2",
        item_turn_cursor="recent-2",
        backwards_cursor="back-1",
        recent_cursor=None,
        pending_backwards_cursor="back-2",
        history_complete=True,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    full_call = client.calls[-1]
    assert full_call[0] == "thread_turns_list"
    assert full_call[1]["cursor"] == "recent-2"
    assert full_call[1]["sort_direction"] == "asc"
    assert result.complete is True
    assert result.backwards_cursor == "back-2"
    assert [item.item_id for item in await store.list_thread_items(lane=lane.id)] == ["item-2"]


@pytest.mark.asyncio
async def test_complete_history_reconciles_two_new_turns(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-3")], backwards_cursor="back-3"),
        recent_turn_pages={
            "back-1": ThreadTurnsPage(data=[_turn("turn-1")], next_cursor="recent-2"),
            "recent-2": ThreadTurnsPage(data=[_turn("turn-2")], next_cursor="recent-3"),
            "recent-3": ThreadTurnsPage(data=[_turn("turn-3")]),
        },
        item_pages={
            ("turn-2", None): ThreadItemsPage(),
            ("turn-3", None): ThreadItemsPage(),
        },
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        backwards_cursor="back-1",
        history_complete=True,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert result.complete is True
    assert result.turns_indexed == 2
    assert result.backwards_cursor == "back-3"
    assert result.recent_cursor is None
    assert result.pending_backwards_cursor is None
    assert {turn.turn_id for turn in await store.list_thread_turns(lane=lane.id)} == {
        "turn-2",
        "turn-3",
    }
    assert [
        (call[1].get("cursor"), call[1].get("sort_direction"))
        for call in client.calls
        if call[0] == "thread_turns_list"
    ] == [("back-1", "asc"), ("recent-2", "asc"), ("recent-3", "asc")]


@pytest.mark.asyncio
async def test_partial_older_backfill_reconciles_recent_before_descending(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-3")], backwards_cursor="back-3"),
        recent_turn_pages={
            "back-1": ThreadTurnsPage(data=[_turn("turn-1")], next_cursor="recent-2"),
            "recent-2": ThreadTurnsPage(data=[_turn("turn-2")], next_cursor="recent-3"),
            "recent-3": ThreadTurnsPage(data=[_turn("turn-3")]),
        },
        turn_pages={"older-2": ThreadTurnsPage(data=[_turn("turn-old")])},
        item_pages={
            ("turn-2", None): ThreadItemsPage(),
            ("turn-3", None): ThreadItemsPage(),
            ("turn-old", None): ThreadItemsPage(),
        },
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor="older-2",
        backwards_cursor="back-1",
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert result.complete is True
    assert result.turns_indexed == 3
    assert result.backwards_cursor == "back-3"
    calls = [
        (call[1].get("cursor"), call[1].get("sort_direction"))
        for call in client.calls
        if call[0] == "thread_turns_list"
    ]
    assert calls == [
        ("back-1", "asc"),
        ("recent-2", "asc"),
        ("recent-3", "asc"),
        ("older-2", "desc"),
    ]


@pytest.mark.asyncio
async def test_recent_budget_interruption_resumes_without_loss(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-3")], backwards_cursor="back-3"),
        recent_turn_pages={
            "back-1": ThreadTurnsPage(data=[_turn("turn-1")], next_cursor="recent-2"),
            "recent-2": ThreadTurnsPage(data=[_turn("turn-2")], next_cursor="recent-3"),
            "recent-3": ThreadTurnsPage(data=[_turn("turn-3")]),
        },
        item_pages={
            ("turn-2", None): ThreadItemsPage(),
            ("turn-3", None): ThreadItemsPage(),
        },
    )

    first = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        backwards_cursor="back-1",
        history_complete=True,
        max_turns=1,
        max_items=10,
        max_seconds=5,
    )

    assert first.item_turn_id == "turn-2"
    assert first.item_turn_cursor == "recent-2"
    assert first.recent_cursor == "recent-3"
    assert first.pending_backwards_cursor == "back-3"
    assert first.backwards_cursor == "back-1"

    second = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        cursor=first.cursor,
        item_turn_id=first.item_turn_id,
        item_turn_cursor=first.item_turn_cursor,
        item_turn_direction=first.item_turn_direction,
        item_cursor=first.item_cursor,
        cursor_guard=first.cursor_guard,
        backwards_cursor=first.backwards_cursor,
        recent_cursor=first.recent_cursor,
        pending_backwards_cursor=first.pending_backwards_cursor,
        history_complete=True,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert second.complete is True
    assert second.backwards_cursor == "back-3"
    assert {turn.turn_id for turn in await store.list_thread_turns(lane=lane.id)} == {
        "turn-2",
        "turn-3",
    }


@pytest.mark.asyncio
async def test_unchanged_complete_sync_indexes_no_turns(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-1")], backwards_cursor="back-1"),
        recent_turn_pages={"back-1": ThreadTurnsPage(data=[_turn("turn-1")])},
    )

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        backwards_cursor="back-1",
        history_complete=True,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert result.complete is True
    assert result.turns_indexed == 0
    assert result.items_indexed == 0
    assert result.backwards_cursor == "back-1"
    assert await store.list_thread_turns(lane=lane.id) == []


@pytest.mark.asyncio
async def test_recent_cursor_cycle_and_anchor_mismatch_are_rejected(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    cycle_client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-2")], backwards_cursor="back-2"),
        recent_turn_pages={
            "back-1": ThreadTurnsPage(data=[_turn("turn-1")], next_cursor="recent-2"),
            "recent-2": ThreadTurnsPage(data=[_turn("turn-2")], next_cursor="back-1"),
        },
    )

    with pytest.raises(ProtocolError, match="repeated pagination cursor 'back-1'"):
        await backfill_codex_history(
            client=cycle_client,
            registry=store,
            lane=lane,
            backwards_cursor="back-1",
            history_complete=True,
            max_turns=10,
            max_items=10,
            max_seconds=5,
        )

    mismatch_client = _BackfillClient(
        initial_page=ThreadTurnsPage(data=[_turn("turn-2")], backwards_cursor="back-2"),
        recent_turn_pages={"back-1": ThreadTurnsPage()},
    )
    with pytest.raises(ProtocolError, match="anchor cursor returned no turn"):
        await backfill_codex_history(
            client=mismatch_client,
            registry=store,
            lane=lane,
            backwards_cursor="back-1",
            history_complete=True,
            max_turns=10,
            max_items=10,
            max_seconds=5,
        )


@pytest.mark.asyncio
async def test_complete_history_only_restores_live_observation(store: Registry) -> None:
    lane = await store.add_lane(id="thread-1", handle="@one", source="own")
    client = _BackfillClient()

    result = await backfill_codex_history(
        client=client,
        registry=store,
        lane=lane,
        history_complete=True,
        max_turns=10,
        max_items=10,
        max_seconds=5,
    )

    assert [call[0] for call in client.calls] == ["thread_resume_full"]
    assert isinstance(client.calls[0][1]["initial_turns_page"], ThreadResumeInitialTurnsPageParams)
    assert result.complete is True
    assert result.pages_scanned == 0
