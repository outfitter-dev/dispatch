"""Bounded incremental App Server history backfill."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from outfitter.dispatch.client.errors import AppServerError, ProtocolError
from outfitter.dispatch.client.models import (
    ThreadItemsPage,
    ThreadResumeInitialTurnsPageParams,
    ThreadTurn,
    ThreadTurnsPage,
)
from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.contracts.context import LaneClient
from outfitter.dispatch.registry.models import Lane
from outfitter.dispatch.registry.store import Registry

from .history_index import index_codex_items_page, index_codex_turns_page

HistoryCapability = Literal["unknown", "supported", "turn-page-fallback", "unsupported"]
SortDirection = Literal["asc", "desc"]
MonotonicClock = Callable[[], float]

_CAPABILITY_ERROR_CODES = {-32601, -32602}
_SOURCE = "codex-app-server"
_FALLBACK_SOURCE = "codex-app-server:thread-turns-full"
_UNSUPPORTED_SOURCE = "metadata-jsonl-fallback"


@dataclass(frozen=True)
class HistoryBackfillResult:
    """Durable progress for recent reconciliation and older backfill."""

    cursor: str | None
    item_turn_id: str | None
    item_turn_cursor: str | None
    item_turn_direction: SortDirection | None
    item_cursor: str | None
    backwards_cursor: str | None
    recent_cursor: str | None
    pending_backwards_cursor: str | None
    complete: bool
    capability: HistoryCapability
    observation_enabled: bool
    cursor_guard: str | None
    pages_scanned: int
    turns_indexed: int
    items_indexed: int
    bytes_scanned: int
    truncated: bool
    source: str = _SOURCE


async def backfill_codex_history(
    *,
    client: LaneClient,
    registry: Registry,
    lane: Lane,
    cursor: str | None = None,
    item_turn_id: str | None = None,
    item_turn_cursor: str | None = None,
    item_turn_direction: SortDirection | None = None,
    item_cursor: str | None = None,
    backwards_cursor: str | None = None,
    recent_cursor: str | None = None,
    pending_backwards_cursor: str | None = None,
    cursor_guard: str | None = None,
    history_complete: bool = False,
    history_capability: HistoryCapability = "unknown",
    max_turns: int,
    max_items: int,
    max_seconds: float,
    max_bytes: int | None = None,
    capture: CapturePolicy | None = None,
    monotonic: MonotonicClock = time.monotonic,
) -> HistoryBackfillResult:
    """Reconcile recent turns first, then resume bounded older history."""

    started = monotonic()
    deadline = started + max_seconds
    initial_request = ThreadResumeInitialTurnsPageParams(
        limit=1,
        sort_direction="desc",
        items_view="notLoaded",
    )
    try:
        resumed = await _within_deadline(
            client.thread_resume_full(
                lane.id,
                exclude_turns=True,
                initial_turns_page=initial_request,
            ),
            deadline=deadline,
            monotonic=monotonic,
        )
    except AppServerError as exc:
        if exc.code not in _CAPABILITY_ERROR_CODES:
            raise
        try:
            await _within_deadline(
                client.thread_resume(lane.id, exclude_turns=True),
                deadline=deadline,
                monotonic=monotonic,
            )
        except AppServerError as resume_exc:
            if resume_exc.code not in _CAPABILITY_ERROR_CODES:
                raise
            observation_enabled = False
        else:
            observation_enabled = True
        return _unsupported_result(
            cursor,
            item_turn_id,
            item_turn_cursor,
            item_turn_direction,
            item_cursor,
            backwards_cursor=backwards_cursor,
            recent_cursor=recent_cursor,
            pending_backwards_cursor=pending_backwards_cursor,
            cursor_guard=cursor_guard,
            observation_enabled=observation_enabled,
        )

    initial_page = resumed.initial_turns_page
    if initial_page is None:
        raise ProtocolError("thread/resume omitted the requested initial turns page")
    if len(initial_page.data) > 1:
        raise ProtocolError("initial thread history exceeded the requested one-turn limit")

    first_sync = (
        cursor is None
        and item_turn_id is None
        and backwards_cursor is None
        and pending_backwards_cursor is None
        and not history_complete
    )
    turn_page: ThreadTurnsPage | None = None
    if pending_backwards_cursor is None:
        if first_sync:
            pending_backwards_cursor = initial_page.backwards_cursor
            turn_page = initial_page
        elif backwards_cursor is not None:
            pending_backwards_cursor = initial_page.backwards_cursor
            if pending_backwards_cursor is None:
                raise ProtocolError("initial thread history omitted its backwards cursor")
            recent_cursor = backwards_cursor

    pages_scanned = 0
    turns_indexed = 0
    items_indexed = 0
    bytes_scanned = 0
    guard = _CursorGuard.from_hex(cursor_guard)
    guard.add("turn:desc", cursor)
    guard.add("turn:asc", recent_cursor)
    guard.add(f"item:{item_turn_id or ''}", item_cursor)
    pending_turn_counted = False
    used_fallback = history_capability == "turn-page-fallback"

    while True:
        if _budget_reached(
            started,
            monotonic,
            turns_indexed,
            items_indexed,
            bytes_scanned,
            max_turns,
            max_items,
            max_seconds,
            max_bytes,
        ):
            return _result(
                cursor=cursor,
                item_turn_id=item_turn_id,
                item_turn_cursor=item_turn_cursor,
                item_turn_direction=item_turn_direction,
                item_cursor=item_cursor,
                backwards_cursor=backwards_cursor,
                recent_cursor=recent_cursor,
                pending_backwards_cursor=pending_backwards_cursor,
                cursor_guard=guard.as_hex(),
                pages_scanned=pages_scanned,
                turns_indexed=turns_indexed,
                items_indexed=items_indexed,
                bytes_scanned=bytes_scanned,
                truncated=True,
                used_fallback=used_fallback,
            )

        reconciling_recent = backwards_cursor is not None and pending_backwards_cursor is not None

        progress = _BackfillProgress(
            cursor=cursor,
            item_turn_id=item_turn_id,
            item_turn_cursor=item_turn_cursor,
            item_turn_direction=item_turn_direction,
            item_cursor=item_cursor,
            backwards_cursor=backwards_cursor,
            recent_cursor=recent_cursor,
            pending_backwards_cursor=pending_backwards_cursor,
            history_complete=history_complete,
            pages_scanned=pages_scanned,
            turns_indexed=turns_indexed,
            items_indexed=items_indexed,
            bytes_scanned=bytes_scanned,
            pending_turn_counted=pending_turn_counted,
            used_fallback=used_fallback,
            guard=guard,
        )
        if item_turn_id is not None:
            terminal = await _hydrate_pending_turn(
                progress=progress,
                client=client,
                registry=registry,
                lane=lane,
                capture=capture,
                reconciling_recent=reconciling_recent,
                max_items=max_items,
                max_bytes=max_bytes,
                deadline=deadline,
                monotonic=monotonic,
            )
        else:
            terminal = await _advance_turn_page(
                progress=progress,
                initial_page=initial_page,
                turn_page=turn_page,
                client=client,
                registry=registry,
                lane=lane,
                capture=capture,
                reconciling_recent=reconciling_recent,
                max_bytes=max_bytes,
                deadline=deadline,
                monotonic=monotonic,
            )
            turn_page = None
        if terminal is not None:
            return terminal

        cursor = progress.cursor
        item_turn_id = progress.item_turn_id
        item_turn_cursor = progress.item_turn_cursor
        item_turn_direction = progress.item_turn_direction
        item_cursor = progress.item_cursor
        backwards_cursor = progress.backwards_cursor
        recent_cursor = progress.recent_cursor
        pending_backwards_cursor = progress.pending_backwards_cursor
        pages_scanned = progress.pages_scanned
        turns_indexed = progress.turns_indexed
        items_indexed = progress.items_indexed
        bytes_scanned = progress.bytes_scanned
        pending_turn_counted = progress.pending_turn_counted
        used_fallback = progress.used_fallback


@dataclass
class _BackfillProgress:
    """Mutable durable progress shared by one bounded backfill invocation."""

    cursor: str | None
    item_turn_id: str | None
    item_turn_cursor: str | None
    item_turn_direction: SortDirection | None
    item_cursor: str | None
    backwards_cursor: str | None
    recent_cursor: str | None
    pending_backwards_cursor: str | None
    history_complete: bool
    pages_scanned: int
    turns_indexed: int
    items_indexed: int
    bytes_scanned: int
    pending_turn_counted: bool
    used_fallback: bool
    guard: _CursorGuard

    def result(self, *, truncated: bool) -> HistoryBackfillResult:
        return _result(
            cursor=self.cursor,
            item_turn_id=self.item_turn_id,
            item_turn_cursor=self.item_turn_cursor,
            item_turn_direction=self.item_turn_direction,
            item_cursor=self.item_cursor,
            backwards_cursor=self.backwards_cursor,
            recent_cursor=self.recent_cursor,
            pending_backwards_cursor=self.pending_backwards_cursor,
            cursor_guard=self.guard.as_hex(),
            pages_scanned=self.pages_scanned,
            turns_indexed=self.turns_indexed,
            items_indexed=self.items_indexed,
            bytes_scanned=self.bytes_scanned,
            truncated=truncated,
            used_fallback=self.used_fallback,
        )

    def unsupported(self) -> HistoryBackfillResult:
        return _unsupported_result(
            self.cursor,
            self.item_turn_id,
            self.item_turn_cursor,
            self.item_turn_direction,
            self.item_cursor,
            backwards_cursor=self.backwards_cursor,
            recent_cursor=self.recent_cursor,
            pending_backwards_cursor=self.pending_backwards_cursor,
            cursor_guard=self.guard.as_hex(),
            pages_scanned=self.pages_scanned,
            turns_indexed=self.turns_indexed,
            items_indexed=self.items_indexed,
            bytes_scanned=self.bytes_scanned,
        )

    def finish_pending_turn(self) -> HistoryBackfillResult | None:
        self.item_turn_id = None
        self.item_turn_cursor = None
        self.item_turn_direction = None
        self.item_cursor = None
        self.pending_turn_counted = False
        self.backwards_cursor, self.pending_backwards_cursor = _promote_recent_if_finished(
            self.backwards_cursor,
            self.recent_cursor,
            self.pending_backwards_cursor,
        )
        if self.history_complete and self.pending_backwards_cursor is None:
            return self.complete()
        if self.cursor is None and self.pending_backwards_cursor is None:
            return self.complete()
        return None

    def complete(self) -> HistoryBackfillResult:
        return _complete_result(
            backwards_cursor=self.backwards_cursor,
            pages_scanned=self.pages_scanned,
            turns_indexed=self.turns_indexed,
            items_indexed=self.items_indexed,
            bytes_scanned=self.bytes_scanned,
            used_fallback=self.used_fallback,
        )


async def _hydrate_pending_turn(
    *,
    progress: _BackfillProgress,
    client: LaneClient,
    registry: Registry,
    lane: Lane,
    capture: CapturePolicy | None,
    reconciling_recent: bool,
    max_items: int,
    max_bytes: int | None,
    deadline: float,
    monotonic: MonotonicClock,
) -> HistoryBackfillResult | None:
    """Hydrate one pending turn through native items or exact-turn fallback."""

    turn_id = progress.item_turn_id
    if turn_id is None:
        raise ProtocolError("pending-turn hydration requires a turn id")
    remaining_items = max_items - progress.items_indexed
    try:
        item_page = await _within_deadline(
            _items_page(
                client,
                lane.id,
                turn_id=turn_id,
                cursor=progress.item_cursor,
                limit=remaining_items,
            ),
            deadline=deadline,
            monotonic=monotonic,
        )
    except AppServerError as exc:
        if exc.code not in _CAPABILITY_ERROR_CODES:
            raise
        fallback_direction = progress.item_turn_direction or (
            "asc" if reconciling_recent else "desc"
        )
        try:
            full_page = await _within_deadline(
                _turns_page(
                    client,
                    lane.id,
                    cursor=progress.item_turn_cursor,
                    items_view="full",
                    sort_direction=fallback_direction,
                ),
                deadline=deadline,
                monotonic=monotonic,
            )
        except AppServerError as turn_exc:
            if turn_exc.code not in _CAPABILITY_ERROR_CODES:
                raise
            return progress.unsupported()

        response_bytes = _serialized_size(full_page)
        bytes_before_response = progress.bytes_scanned
        progress.pages_scanned += 1
        progress.bytes_scanned += response_bytes
        progress.used_fallback = True
        if max_bytes is not None and response_bytes > max_bytes - bytes_before_response:
            return progress.result(truncated=True)

        full_turn = _find_fallback_turn(full_page, turn_id)
        if full_turn is None:
            if progress.item_turn_cursor is not None and progress.item_turn_direction is None:
                actual = full_page.data[0].id if full_page.data else None
                raise ProtocolError(
                    f"full turn fallback returned {actual!r}, expected {turn_id!r}"
                ) from exc
            next_fallback_cursor = full_page.next_cursor
            if next_fallback_cursor is None:
                raise ProtocolError(f"full turn fallback could not find {turn_id!r}") from exc
            progress.guard.check_and_add(
                f"fallback:{fallback_direction}",
                next_fallback_cursor,
            )
            progress.item_turn_cursor = next_fallback_cursor
            progress.item_turn_direction = fallback_direction
            return None

        if progress.item_turn_cursor is None and full_page.backwards_cursor is not None:
            progress.item_turn_cursor = full_page.backwards_cursor
            progress.item_turn_direction = "asc"
        if len(full_turn.items or []) > remaining_items:
            return progress.result(truncated=True)

        counts = await index_codex_turns_page(registry, lane, [full_turn], capture)
        if not progress.pending_turn_counted:
            progress.turns_indexed += counts.turns
        progress.items_indexed += counts.items
        return progress.finish_pending_turn()

    if len(item_page.data) > remaining_items:
        raise ProtocolError("thread/items/list exceeded the requested item limit")
    response_bytes = _serialized_size(item_page)
    bytes_before_response = progress.bytes_scanned
    progress.pages_scanned += 1
    progress.bytes_scanned += response_bytes
    if max_bytes is not None and response_bytes > max_bytes - bytes_before_response:
        return progress.result(truncated=True)

    counts = await index_codex_items_page(
        registry,
        lane,
        turn_id,
        item_page.data,
        capture,
    )
    progress.items_indexed += counts.items
    next_item_cursor = item_page.next_cursor
    if next_item_cursor is not None:
        progress.guard.check_and_add(f"item:{turn_id}", next_item_cursor)
        progress.item_cursor = next_item_cursor
        return None
    return progress.finish_pending_turn()


async def _advance_turn_page(
    *,
    progress: _BackfillProgress,
    initial_page: ThreadTurnsPage,
    turn_page: ThreadTurnsPage | None,
    client: LaneClient,
    registry: Registry,
    lane: Lane,
    capture: CapturePolicy | None,
    reconciling_recent: bool,
    max_bytes: int | None,
    deadline: float,
    monotonic: MonotonicClock,
) -> HistoryBackfillResult | None:
    """Advance one recent or older turn page and establish pending hydration."""

    if progress.history_complete and progress.pending_backwards_cursor is None:
        return progress.complete()

    requested_cursor = progress.recent_cursor if reconciling_recent else progress.cursor
    sort_direction: SortDirection = "asc" if reconciling_recent else "desc"
    page = turn_page
    if page is None:
        try:
            page = await _within_deadline(
                _turns_page(
                    client,
                    lane.id,
                    cursor=requested_cursor,
                    items_view="notLoaded",
                    sort_direction=sort_direction,
                ),
                deadline=deadline,
                monotonic=monotonic,
            )
        except AppServerError as exc:
            if exc.code not in _CAPABILITY_ERROR_CODES:
                raise
            return progress.unsupported()

    if len(page.data) > 1:
        raise ProtocolError("thread/turns/list exceeded the requested one-turn limit")
    response_bytes = _serialized_size(page)
    bytes_before_response = progress.bytes_scanned
    progress.bytes_scanned += response_bytes
    progress.pages_scanned += 1
    if max_bytes is not None and response_bytes > max_bytes - bytes_before_response:
        return progress.result(truncated=True)

    next_cursor = page.next_cursor
    if next_cursor is not None:
        progress.guard.check_and_add(f"turn:{sort_direction}", next_cursor)

    anchor_page = reconciling_recent and requested_cursor == progress.backwards_cursor
    if anchor_page:
        if not page.data:
            raise ProtocolError("recent history anchor cursor returned no turn")
        progress.recent_cursor = next_cursor
        (
            progress.backwards_cursor,
            progress.pending_backwards_cursor,
        ) = _promote_recent_if_finished(
            progress.backwards_cursor,
            progress.recent_cursor,
            progress.pending_backwards_cursor,
        )
        return None

    if not page.data:
        if reconciling_recent:
            if next_cursor is not None:
                raise ProtocolError("recent history returned an empty intermediate page")
            progress.backwards_cursor = progress.pending_backwards_cursor
            progress.pending_backwards_cursor = None
            progress.recent_cursor = None
            return None
        progress.cursor = next_cursor
        return progress.complete() if next_cursor is None else progress.result(truncated=True)

    turn = page.data[0]
    counts = await index_codex_turns_page(registry, lane, [turn], capture)
    progress.turns_indexed += counts.turns
    if reconciling_recent:
        progress.recent_cursor = next_cursor
    else:
        progress.cursor = next_cursor
    progress.item_turn_id = turn.id
    if page is initial_page and initial_page.backwards_cursor is not None:
        progress.item_turn_cursor = initial_page.backwards_cursor
        progress.item_turn_direction = "asc"
    else:
        progress.item_turn_cursor = requested_cursor
        progress.item_turn_direction = sort_direction
    progress.item_cursor = None
    progress.pending_turn_counted = True
    return None


async def _turns_page(
    client: LaneClient,
    thread_id: str,
    *,
    cursor: str | None,
    items_view: Literal["notLoaded", "full"],
    sort_direction: SortDirection = "desc",
) -> ThreadTurnsPage:
    return await client.thread_turns_list(
        thread_id,
        cursor=cursor,
        limit=1,
        sort_direction=sort_direction,
        items_view=items_view,
    )


async def _items_page(
    client: LaneClient,
    thread_id: str,
    *,
    turn_id: str,
    cursor: str | None,
    limit: int,
) -> ThreadItemsPage:
    return await client.thread_items_list(
        thread_id,
        turn_id=turn_id,
        cursor=cursor,
        limit=limit,
        sort_direction="desc",
    )


async def _within_deadline[T](
    awaitable: Awaitable[T],
    *,
    deadline: float,
    monotonic: MonotonicClock,
) -> T:
    remaining = max(0.0, deadline - monotonic())
    try:
        return await asyncio.wait_for(awaitable, timeout=remaining)
    except TimeoutError as exc:
        raise ProtocolError("App Server history sync exceeded max_seconds") from exc


def _find_fallback_turn(page: ThreadTurnsPage, expected_turn_id: str) -> ThreadTurn | None:
    if len(page.data) > 1:
        raise ProtocolError("full turn fallback exceeded the requested one-turn limit")
    if page.data and page.data[0].id == expected_turn_id:
        return page.data[0]
    return None


class _CursorGuard:
    """Bounded durable Bloom guard that fails closed on cursor cycles."""

    _BYTE_COUNT = 16_384
    _HASH_COUNT = 6

    def __init__(self, bits: bytearray | None = None) -> None:
        self._bits = bits or bytearray(self._BYTE_COUNT)

    @classmethod
    def from_hex(cls, value: str | None) -> _CursorGuard:
        if value is None:
            return cls()
        try:
            bits = bytearray.fromhex(value)
        except ValueError as exc:
            raise ProtocolError("history cursor guard is not valid hexadecimal") from exc
        if len(bits) != cls._BYTE_COUNT:
            raise ProtocolError("history cursor guard has an invalid size")
        return cls(bits)

    def as_hex(self) -> str:
        return self._bits.hex()

    def add(self, namespace: str, cursor: str | None) -> None:
        if cursor is None:
            return
        for bit in self._positions(namespace, cursor):
            self._bits[bit // 8] |= 1 << (bit % 8)

    def check_and_add(self, namespace: str, cursor: str) -> None:
        positions = self._positions(namespace, cursor)
        if all(self._bits[bit // 8] & (1 << (bit % 8)) for bit in positions):
            raise ProtocolError(f"thread history repeated pagination cursor {cursor!r}")
        for bit in positions:
            self._bits[bit // 8] |= 1 << (bit % 8)

    @classmethod
    def _positions(cls, namespace: str, cursor: str) -> tuple[int, ...]:
        digest = hashlib.sha256(f"{namespace}\0{cursor}".encode()).digest()
        bit_count = cls._BYTE_COUNT * 8
        return tuple(
            int.from_bytes(digest[index * 4 : index * 4 + 4], "big") % bit_count
            for index in range(cls._HASH_COUNT)
        )


def _promote_recent_if_finished(
    backwards_cursor: str | None,
    recent_cursor: str | None,
    pending_backwards_cursor: str | None,
) -> tuple[str | None, str | None]:
    if pending_backwards_cursor is not None and recent_cursor is None:
        return pending_backwards_cursor, None
    return backwards_cursor, pending_backwards_cursor


def _budget_reached(
    started: float,
    monotonic: MonotonicClock,
    turns: int,
    items: int,
    bytes_scanned: int,
    max_turns: int,
    max_items: int,
    max_seconds: float,
    max_bytes: int | None,
) -> bool:
    return (
        turns >= max_turns
        or items >= max_items
        or monotonic() - started >= max_seconds
        or (max_bytes is not None and bytes_scanned >= max_bytes)
    )


def _serialized_size(page: ThreadTurnsPage | ThreadItemsPage) -> int:
    payload = page.model_dump(by_alias=True, exclude_none=True)
    return len(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())


def _complete_result(
    *,
    backwards_cursor: str | None,
    pages_scanned: int,
    turns_indexed: int,
    items_indexed: int,
    bytes_scanned: int,
    truncated: bool = False,
    used_fallback: bool = False,
) -> HistoryBackfillResult:
    return _result(
        cursor=None,
        item_turn_id=None,
        item_turn_cursor=None,
        item_turn_direction=None,
        item_cursor=None,
        backwards_cursor=backwards_cursor,
        recent_cursor=None,
        pending_backwards_cursor=None,
        complete=True,
        pages_scanned=pages_scanned,
        turns_indexed=turns_indexed,
        items_indexed=items_indexed,
        bytes_scanned=bytes_scanned,
        truncated=truncated,
        used_fallback=used_fallback,
    )


def _result(
    *,
    cursor: str | None,
    item_turn_id: str | None,
    item_turn_cursor: str | None,
    item_turn_direction: SortDirection | None,
    item_cursor: str | None,
    backwards_cursor: str | None = None,
    recent_cursor: str | None = None,
    pending_backwards_cursor: str | None = None,
    cursor_guard: str | None = None,
    complete: bool = False,
    pages_scanned: int = 0,
    turns_indexed: int = 0,
    items_indexed: int = 0,
    bytes_scanned: int = 0,
    truncated: bool = False,
    used_fallback: bool = False,
) -> HistoryBackfillResult:
    return HistoryBackfillResult(
        cursor=cursor,
        item_turn_id=item_turn_id,
        item_turn_cursor=item_turn_cursor,
        item_turn_direction=item_turn_direction,
        item_cursor=item_cursor,
        backwards_cursor=backwards_cursor,
        recent_cursor=recent_cursor,
        pending_backwards_cursor=pending_backwards_cursor,
        complete=complete,
        capability="turn-page-fallback" if used_fallback else "supported",
        observation_enabled=True,
        cursor_guard=cursor_guard,
        pages_scanned=pages_scanned,
        turns_indexed=turns_indexed,
        items_indexed=items_indexed,
        bytes_scanned=bytes_scanned,
        truncated=truncated,
        source=_FALLBACK_SOURCE if used_fallback else _SOURCE,
    )


def _unsupported_result(
    cursor: str | None,
    item_turn_id: str | None,
    item_turn_cursor: str | None,
    item_turn_direction: SortDirection | None,
    item_cursor: str | None,
    *,
    backwards_cursor: str | None = None,
    recent_cursor: str | None = None,
    pending_backwards_cursor: str | None = None,
    cursor_guard: str | None = None,
    pages_scanned: int = 0,
    turns_indexed: int = 0,
    items_indexed: int = 0,
    bytes_scanned: int = 0,
    observation_enabled: bool = True,
) -> HistoryBackfillResult:
    return HistoryBackfillResult(
        cursor=cursor,
        item_turn_id=item_turn_id,
        item_turn_cursor=item_turn_cursor,
        item_turn_direction=item_turn_direction,
        item_cursor=item_cursor,
        backwards_cursor=backwards_cursor,
        recent_cursor=recent_cursor,
        pending_backwards_cursor=pending_backwards_cursor,
        complete=False,
        capability="unsupported",
        observation_enabled=observation_enabled,
        cursor_guard=cursor_guard,
        pages_scanned=pages_scanned,
        turns_indexed=turns_indexed,
        items_indexed=items_indexed,
        bytes_scanned=bytes_scanned,
        truncated=False,
        source=_UNSUPPORTED_SOURCE,
    )
