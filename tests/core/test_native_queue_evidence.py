"""Bounded positive evidence from the provider-native queued-submission list."""

from __future__ import annotations

import pytest

from outfitter.dispatch.client.errors import ProtocolError
from outfitter.dispatch.client.native_queue import QueuedSubmission, ThreadQueuePage
from outfitter.dispatch.core.native_queue_evidence import find_queued_submission
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx


def _submission(
    submission_id: str,
    client_user_message_id: str,
    text: str,
) -> QueuedSubmission:
    return QueuedSubmission.model_validate(
        {
            "id": submission_id,
            "clientUserMessageId": client_user_message_id,
            "input": [{"type": "text", "text": text}],
        }
    )


class QueuePagesClient(FakeLaneClient):
    def __init__(self, pages: dict[str | None, ThreadQueuePage]) -> None:
        super().__init__()
        self.pages = pages

    async def thread_queue_list(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> ThreadQueuePage:
        self._record("thread_queue_list", thread_id=thread_id, cursor=cursor, limit=limit)
        return self.pages[cursor]


async def _find(client: QueuePagesClient) -> QueuedSubmission | None:
    store = await Registry.open()
    try:
        await store.add_lane(id="thread-1", handle="@thread", source="attached", status="idle")
        return await find_queued_submission(
            make_ctx(store, client), "thread-1", "receipt-1", "hello"
        )
    finally:
        await store.close()


async def test_absent_submission_remains_inconclusive() -> None:
    client = QueuePagesClient(
        {None: ThreadQueuePage(data=[_submission("other", "other-receipt", "hello")])}
    )

    assert await _find(client) is None


async def test_exact_submission_is_found_on_second_cursor_page() -> None:
    expected = _submission("submission-1", "receipt-1", "hello")
    client = QueuePagesClient(
        {
            None: ThreadQueuePage(data=[], next_cursor="page-2"),
            "page-2": ThreadQueuePage(data=[expected]),
        }
    )

    assert await _find(client) == expected
    assert [params["cursor"] for _, params in client.calls] == [None, "page-2"]


async def test_matching_client_id_with_different_text_is_rejected() -> None:
    client = QueuePagesClient(
        {None: ThreadQueuePage(data=[_submission("submission-1", "receipt-1", "different")])}
    )

    with pytest.raises(ProtocolError, match="conflicting input"):
        await _find(client)


async def test_duplicate_client_message_ids_are_rejected() -> None:
    client = QueuePagesClient(
        {
            None: ThreadQueuePage(
                data=[
                    _submission("submission-1", "receipt-1", "hello"),
                    _submission("submission-2", "receipt-1", "hello"),
                ]
            )
        }
    )

    with pytest.raises(ProtocolError, match="duplicate client message IDs"):
        await _find(client)


async def test_repeated_pagination_cursor_is_rejected() -> None:
    client = QueuePagesClient(
        {
            None: ThreadQueuePage(data=[], next_cursor="same"),
            "same": ThreadQueuePage(data=[], next_cursor="same"),
        }
    )

    with pytest.raises(ProtocolError, match="repeated pagination cursor"):
        await _find(client)


async def test_more_than_four_pages_is_rejected_without_fetching_a_fifth() -> None:
    client = QueuePagesClient(
        {
            None: ThreadQueuePage(data=[], next_cursor="page-2"),
            "page-2": ThreadQueuePage(data=[], next_cursor="page-3"),
            "page-3": ThreadQueuePage(data=[], next_cursor="page-4"),
            "page-4": ThreadQueuePage(data=[], next_cursor="page-5"),
        }
    )

    with pytest.raises(ProtocolError, match="page budget"):
        await _find(client)
    assert len(client.calls) == 4


async def test_serialized_pages_over_one_megabyte_are_rejected() -> None:
    oversized = _submission("other", "other-receipt", "x" * 1_000_001)
    client = QueuePagesClient({None: ThreadQueuePage(data=[oversized])})

    with pytest.raises(ProtocolError, match="byte budget"):
        await _find(client)
