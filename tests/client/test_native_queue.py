"""Native queue protocol without taking ownership of the target writer."""

from outfitter.dispatch.client.client import AppServerClient
from tests.client.conftest import FakeTransport
from tests.client.test_client import _result_for


async def test_native_queue_add_preserves_correlation_without_resume(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, wire = client
    wire.auto = _result_for(
        "thread/queue/add",
        {
            "queuedSubmission": {
                "id": "submission-1",
                "clientUserMessageId": "receipt-1",
                "input": [{"type": "text", "text": "hello"}],
            }
        },
    )
    receipt = await c.thread_queue_add("target", "hello", client_user_message_id="receipt-1")
    assert receipt.id == "submission-1"
    assert receipt.client_user_message_id == "receipt-1"
    assert [m["method"] for m in wire.sent] == ["thread/queue/add"]
    assert wire.sent[0]["params"] == {
        "threadId": "target",
        "clientUserMessageId": "receipt-1",
        "input": [{"type": "text", "text": "hello"}],
    }


async def test_native_queue_list_is_bounded_and_typed(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, wire = client
    wire.auto = _result_for("thread/queue/list", {"data": [], "nextCursor": "next"})
    page = await c.thread_queue_list("target", cursor="cursor", limit=50)
    assert page.data == [] and page.next_cursor == "next"
    assert wire.sent[0]["params"] == {"threadId": "target", "cursor": "cursor", "limit": 50}
