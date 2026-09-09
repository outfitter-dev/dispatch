"""Positive native queue evidence; absence never permits another submission."""

import json

from outfitter.dispatch.client.errors import ProtocolError
from outfitter.dispatch.client.native_queue import QueuedSubmission
from outfitter.dispatch.contracts.context import Ctx


async def find_queued_submission(
    ctx: Ctx, thread_id: str, receipt_id: str, text: str
) -> QueuedSubmission | None:
    cursor: str | None = None
    seen: set[str] = set()
    matches: list[QueuedSubmission] = []
    total_bytes = 0
    for _ in range(4):
        page = await ctx.client.thread_queue_list(thread_id, cursor=cursor, limit=50)
        total_bytes += len(json.dumps(page.model_dump(mode="json")).encode())
        if total_bytes > 1_000_000:
            raise ProtocolError("native queue evidence exceeds byte budget")
        for entry in page.data:
            if entry.client_user_message_id != receipt_id:
                continue
            if (
                len(entry.input) != 1
                or entry.input[0].type != "text"
                or entry.input[0].text != text
            ):
                raise ProtocolError("native queue client message ID has conflicting input")
            matches.append(entry)
        if page.next_cursor is None:
            if len(matches) > 1:
                raise ProtocolError("native queue has duplicate client message IDs")
            return matches[0] if matches else None
        if page.next_cursor in seen:
            raise ProtocolError("native queue repeated pagination cursor")
        seen.add(page.next_cursor)
        cursor = page.next_cursor
    raise ProtocolError("native queue evidence exceeds page budget")
