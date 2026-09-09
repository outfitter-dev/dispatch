"""Synthetic acceptance and provider-history seams for delivery tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.client.models import (
    SortDirection,
    ThreadTurn,
    ThreadTurnsPage,
    TurnItemsView,
)
from tests.fakes import FakeLaneClient


class AcceptedClient(FakeLaneClient):
    on_accept: Callable[[], Awaitable[None]] | None = None
    legacy_ack = False

    async def turn_start(self, *args: object, **kwargs: object) -> dict[str, object]:
        self._record("turn_start", args=args, **kwargs)
        if self.on_accept is not None:
            await self.on_accept()
        return {"turnId": "turn-1"} if self.legacy_ack else {"turn": {"id": "turn-1"}}


class LostAckClient(AcceptedClient):
    lose_ack = True

    async def turn_start(self, *args: object, **kwargs: object) -> dict[str, object]:
        result = await super().turn_start(*args, **kwargs)
        if self.lose_ack:
            raise TransportError("synthetic acceptance followed by lost acknowledgment")
        return result


class HistoryClient(LostAckClient):
    visible = True
    variant = "exact"

    async def thread_turns_list(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: SortDirection | None = None,
        items_view: TurnItemsView | None = None,
    ) -> ThreadTurnsPage:
        self._record("thread_turns_list", thread_id=thread_id)
        starts = [
            kw
            for name, kw in self.calls
            if name == "turn_start" and isinstance(kw["args"], tuple) and kw["args"][0] == thread_id
        ]
        latest = starts[-1]
        args = latest["args"]
        assert isinstance(args, tuple)
        if self.variant == "timeout":
            raise TimeoutError("synthetic history timeout")
        page = ThreadTurnsPage(
            data=[
                ThreadTurn(
                    id="turn-1",
                    status="completed",
                    items=[
                        {
                            "type": "userMessage",
                            "id": "user-1",
                            "clientId": latest["client_user_message_id"],
                            "content": [{"type": "text", "text": args[1]}],
                        }
                    ],
                )
            ]
            if self.visible
            else []
        )
        if self.variant == "truncated":
            page.next_cursor = "more"
        elif self.variant == "summary":
            page.data[0].items_view = "summary"
        elif self.variant == "wrong-text":
            page.data[0].items[0]["content"] = [{"type": "text", "text": "wrong"}]
        elif self.variant == "assistant":
            page.data[0].items[0]["type"] = "agentMessage"
        elif self.variant == "duplicate":
            page.data.append(page.data[0].model_copy(update={"id": "turn-2"}))
        return page
