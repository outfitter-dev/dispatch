"""Message router: demux one app-server connection into responses, normalized
lane events, and a raw per-lane stream.

Mirrors the verified pattern (the Python SDK's internal router): responses keyed
by request ``id``; notifications and server-requests keyed by ``threadId`` into
per-lane streams plus a global stream. Subscriptions register eagerly so no event
is lost between ``subscribe`` and the first iteration.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from .errors import AppServerError, ClientError
from .events import LaneEvent, project_notification, project_server_request


class Subscription[T]:
    """An async-iterable view of a broadcaster stream. ``None`` is the close
    sentinel (the broadcast item types — LaneEvent, raw dict — are never None)."""

    def __init__(self, queue: asyncio.Queue[T | None], on_close: Callable[[], None]) -> None:
        self._queue = queue
        self._on_close = on_close
        self._closed = False

    def __aiter__(self) -> AsyncIterator[T]:
        return self

    async def __anext__(self) -> T:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self._on_close()


class Broadcaster[T]:
    """Fan-out of items to per-key and global (key=None) subscribers."""

    def __init__(self) -> None:
        self._subs: list[tuple[str | None, asyncio.Queue[T | None]]] = []
        self._closed = False

    def subscribe(self, key: str | None) -> Subscription[T]:
        queue: asyncio.Queue[T | None] = asyncio.Queue()
        entry: tuple[str | None, asyncio.Queue[T | None]] = (key, queue)
        self._subs.append(entry)
        if self._closed:
            queue.put_nowait(None)

        def _remove() -> None:
            if entry in self._subs:
                self._subs.remove(entry)

        return Subscription(queue, _remove)

    def publish(self, key: str, item: T) -> None:
        for sub_key, queue in self._subs:
            if sub_key is None or sub_key == key:
                queue.put_nowait(item)

    def close(self) -> None:
        self._closed = True
        for _, queue in self._subs:
            queue.put_nowait(None)


class Router:
    """Routes parsed wire messages to pending requests and event streams."""

    def __init__(self) -> None:
        self._pending: dict[int, asyncio.Future[dict[str, object]]] = {}
        self.events: Broadcaster[LaneEvent] = Broadcaster()
        self.raw: Broadcaster[dict[str, object]] = Broadcaster()

    def new_request(self, request_id: int) -> asyncio.Future[dict[str, object]]:
        fut: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        return fut

    def handle(self, message: dict[str, object]) -> None:
        mid = message.get("id")
        method = message.get("method")
        if isinstance(mid, int) and ("result" in message or "error" in message):
            self._resolve(mid, message)
        elif isinstance(mid, int) and isinstance(method, str):
            self._server_request(mid, method, message.get("params"))
        elif isinstance(method, str):
            self._notification(method, message.get("params"))

    def _resolve(self, request_id: int, message: dict[str, object]) -> None:
        fut = self._pending.pop(request_id, None)
        if fut is None or fut.done():
            return
        if "error" in message:
            err = message.get("error")
            if isinstance(err, dict):
                code = err.get("code")
                msg = err.get("message")
                fut.set_exception(
                    AppServerError(
                        code if isinstance(code, int) else -1,
                        msg if isinstance(msg, str) else "unknown error",
                        err.get("data"),
                    )
                )
            else:
                fut.set_exception(AppServerError(-1, "malformed error response"))
            return
        result = message.get("result")
        fut.set_result(result if isinstance(result, dict) else {})

    def _server_request(self, request_id: int, method: str, params: object) -> None:
        p = params if isinstance(params, dict) else {}
        lane = p.get("threadId")
        if isinstance(lane, str):
            self.raw.publish(lane, {"id": request_id, "method": method, "params": p})
        event = project_server_request(request_id, method, p)
        if event is not None:
            self.events.publish(event.lane_id, event)

    def _notification(self, method: str, params: object) -> None:
        p = params if isinstance(params, dict) else {}
        lane = p.get("threadId")
        if isinstance(lane, str):
            self.raw.publish(lane, {"method": method, "params": p})
        for event in project_notification(method, p):
            self.events.publish(event.lane_id, event)

    def fail_all(self, exc: ClientError) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        self.events.close()
        self.raw.close()
