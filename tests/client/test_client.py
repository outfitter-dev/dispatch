"""Unit tests for AppServerClient against a fake transport (no real app-server)."""

from __future__ import annotations

import asyncio

import pytest

from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.errors import AppServerError, ProtocolError, TransportError
from outfitter.dispatch.client.events import TurnCompleted

from .conftest import FakeTransport, Responder


def _result_for(method: str, result: dict[str, object]) -> Responder:
    def responder(message: dict[str, object]) -> list[dict[str, object]]:
        if message.get("method") == method and "id" in message:
            return [{"id": message["id"], "result": result}]
        return []

    return responder


async def test_initialize_sends_request_then_initialized_notification(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    fake.auto = _result_for("initialize", {"userAgent": "ua", "codexHome": "/h"})
    result = await c.initialize()
    assert result.codex_home == "/h"
    methods = [m.get("method") for m in fake.sent]
    assert methods == ["initialize", "initialized"]
    assert "id" not in fake.sent[1]  # initialized is a notification


async def test_bounded_request_timeout_discards_pending(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    # No auto-responder: thread/resume never gets a reply, so a bounded wait_for
    # cancels it. The client must drop the pending id (no leak across timeouts).
    c, _fake = client
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(c.thread_resume("STUCK"), 0.05)
    assert c._router._pending == {}


async def test_thread_start_parses_thread_info(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    fake.auto = _result_for("thread/start", {"thread": {"id": "L1", "ephemeral": True}})
    thread = await c.thread_start(cwd="/work", ephemeral=True)
    assert thread.id == "L1"
    assert thread.ephemeral is True
    sent = fake.sent[-1]
    assert sent["params"] == {
        "cwd": "/work",
        "sandbox": "read-only",
        "approvalPolicy": "never",
        "ephemeral": True,
    }


async def test_thread_set_name_sends_verified_method(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    fake.auto = _result_for("thread/name/set", {})
    await c.thread_set_name("L1", "[dispatch] review")
    assert fake.sent[-1] == {
        "id": 1,
        "method": "thread/name/set",
        "params": {"threadId": "L1", "name": "[dispatch] review"},
    }


async def test_thread_read_can_include_turns(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    fake.auto = _result_for("thread/read", {"thread": {"id": "L1", "turns": []}})
    result = await c.thread_read("L1", include_turns=True)
    assert result["thread"] == {"id": "L1", "turns": []}
    assert fake.sent[-1] == {
        "id": 1,
        "method": "thread/read",
        "params": {"threadId": "L1", "includeTurns": True},
    }


async def test_thread_goal_methods(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    goal = {
        "threadId": "L1",
        "objective": "ship",
        "status": "active",
        "tokensUsed": 0,
        "timeUsedSeconds": 0,
        "createdAt": 1,
        "updatedAt": 2,
    }
    fake.auto = _result_for("thread/goal/set", {"goal": goal})
    set_goal = await c.thread_goal_set("L1", objective="ship", token_budget=10)
    assert set_goal.objective == "ship"
    assert fake.sent[-1]["params"] == {"threadId": "L1", "objective": "ship", "tokenBudget": 10}

    fake.auto = _result_for("thread/goal/get", {"goal": goal})
    got = await c.thread_goal_get("L1")
    assert got is not None
    assert got.thread_id == "L1"

    fake.auto = _result_for("thread/goal/clear", {"cleared": True})
    await c.thread_goal_clear("L1")
    assert fake.sent[-1] == {
        "id": 3,
        "method": "thread/goal/clear",
        "params": {"threadId": "L1"},
    }


async def test_thread_fork_rollback_and_compact_methods(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    fake.auto = _result_for("thread/fork", {"thread": {"id": "F1", "forkedFromId": "L1"}})
    fork = await c.thread_fork("L1", cwd="/w", sandbox="workspace-write", ephemeral=True)
    assert fork.id == "F1"
    assert fork.forked_from_id == "L1"
    assert fake.sent[-1]["params"] == {
        "threadId": "L1",
        "cwd": "/w",
        "sandbox": "workspace-write",
        "ephemeral": True,
    }

    fake.auto = _result_for("thread/rollback", {"thread": {"id": "L1"}})
    rolled = await c.thread_rollback("L1", 2)
    assert rolled.id == "L1"
    assert fake.sent[-1] == {
        "id": 2,
        "method": "thread/rollback",
        "params": {"threadId": "L1", "numTurns": 2},
    }

    fake.auto = _result_for("thread/compact/start", {})
    await c.thread_compact_start("L1")
    assert fake.sent[-1] == {
        "id": 3,
        "method": "thread/compact/start",
        "params": {"threadId": "L1"},
    }


async def test_thread_list_reads_data_key(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    fake.auto = _result_for("thread/list", {"data": [{"id": "a"}, {"id": "b"}]})
    threads = await c.thread_list()
    assert [t.id for t in threads] == ["a", "b"]


async def test_request_error_raises_app_server_error(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client

    def responder(message: dict[str, object]) -> list[dict[str, object]]:
        return [{"id": message["id"], "error": {"code": -32600, "message": "unknown variant"}}]

    fake.auto = responder
    with pytest.raises(AppServerError) as exc:
        await c.thread_resume("missing")
    assert exc.value.code == -32600


async def test_eof_fails_pending_request_with_transport_error(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    # No auto-responder: the request stays pending until the stream ends.
    task = asyncio.create_task(c.thread_read("L1"))
    await asyncio.sleep(0)  # let the request flush
    fake.eof()
    with pytest.raises(TransportError):
        await task


async def test_reader_error_fails_pending_request() -> None:
    class BrokenTransport(FakeTransport):
        async def receive(self) -> dict[str, object] | None:
            raise ProtocolError("line too large")

    c = AppServerClient(BrokenTransport())
    await c.start()
    with pytest.raises(ProtocolError, match="line too large"):
        await c.thread_read("L1")


async def test_events_stream_yields_projected_lane_event(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    stream = c.events("L1")
    fake.feed({"method": "turn/completed", "params": {"threadId": "L1", "turnId": "T1"}})
    event = await asyncio.wait_for(stream.__anext__(), timeout=1)
    assert event == TurnCompleted("L1", "T1")


async def test_respond_approval_sends_decision_on_same_stream(
    client: tuple[AppServerClient, FakeTransport],
) -> None:
    c, fake = client
    await c.respond_approval(7, "accept")
    assert fake.sent[-1] == {"id": 7, "result": {"decision": "accept"}}
