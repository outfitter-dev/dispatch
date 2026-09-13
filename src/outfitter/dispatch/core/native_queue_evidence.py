"""Positive native queue evidence; absence never permits another submission."""

import asyncio
import json

from outfitter.dispatch.client.errors import ClientError, ProtocolError
from outfitter.dispatch.client.native_queue import QueuedSubmission
from outfitter.dispatch.contracts.context import Ctx

from .providers import ProviderAction, route_lane

NATIVE_QUEUE_TIMEOUT = 4


class NativeQueueConflict(ProtocolError):
    """Positive queue evidence contradicts a unique matching delivery."""


async def find_queued_submission(
    ctx: Ctx, thread_id: str, receipt_id: str, text: str
) -> QueuedSubmission | None:
    lane = await ctx.registry.find_lane(thread_id)
    if lane is None:
        return None
    route = route_lane(ctx, lane, ProviderAction.QUEUE_EVIDENCE)
    cursor: str | None = None
    seen: set[str] = set()
    matches: list[QueuedSubmission] = []
    total_bytes = 0
    try:
        async with asyncio.timeout(NATIVE_QUEUE_TIMEOUT):
            for _ in range(4):
                route.recheck()
                page = await route.adapter.queue_list(route.target, cursor=cursor, limit=50)
                total_bytes += len(json.dumps(page.model_dump(mode="json")).encode())
                for entry in page.data:
                    if entry.client_user_message_id != receipt_id:
                        continue
                    if (
                        len(entry.input) != 1
                        or entry.input[0].type != "text"
                        or entry.input[0].text != text
                    ):
                        raise NativeQueueConflict(
                            "native queue client message ID has conflicting input"
                        )
                    matches.append(entry)
                if total_bytes > 1_000_000:
                    raise ProtocolError("native queue evidence exceeds byte budget")
                if page.next_cursor is None:
                    if len(matches) > 1:
                        raise NativeQueueConflict("native queue has duplicate client message IDs")
                    return matches[0] if matches else None
                if page.next_cursor in seen:
                    raise ProtocolError("native queue repeated pagination cursor")
                seen.add(page.next_cursor)
                cursor = page.next_cursor
            raise ProtocolError("native queue evidence exceeds page budget")
    except NativeQueueConflict:
        raise
    except (ClientError, TimeoutError) as exc:
        if matches:
            raise NativeQueueConflict(
                "native queue evidence incomplete after matching input"
            ) from exc
        raise
