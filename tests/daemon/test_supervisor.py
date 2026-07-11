"""Supervisor: restart the app-server on crash and restore lane observation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio

from outfitter.dispatch.client.errors import AppServerError
from outfitter.dispatch.client.models import (
    PermissionProfileSummary,
    ThreadInfo,
    ThreadResumeInitialTurnsPageParams,
)
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.core.turn_settings import runtime_settings_for_lane
from outfitter.dispatch.daemon.supervisor import Supervisor
from outfitter.dispatch.registry.models import LaneSync
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeSupervisedClient, make_ctx

_T0 = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open()
    try:
        yield s
    finally:
        await s.close()


async def test_supervisor_restarts_and_restores_lanes_on_crash(store: Registry) -> None:
    await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")
    await store.add_lane(id="O1", handle="@own", source="own", status="idle")
    await store.upsert_lane_runtime_settings(
        runtime_settings_for_lane(
            lane="O1",
            updated_at="2026-06-03T12:00:00+00:00",
            permission_profile=":read-only",
        )
    )
    ctx = make_ctx(store)
    clients: list[FakeSupervisedClient] = []

    async def make_client() -> FakeSupervisedClient:
        client = FakeSupervisedClient()
        clients.append(client)
        return client

    runner = TriggerRunner(ctx, lambda: _T0)
    supervisor = Supervisor(ctx, make_client, lambda: Reactor(ctx, runner).run(), backoff=0)

    first = await make_client()
    task = asyncio.create_task(supervisor.supervise(first))
    await asyncio.sleep(0.05)

    # Owned lanes are resumed for event observation; attached lanes stay
    # metadata-only after restart (ADR-0017).
    assert clients[0].resumed == ["O1"]
    assert any(
        name == "thread_resume" and kw["permission_profile"] == ":read-only"
        for name, kw in clients[0].calls
    )
    assert any(
        name == "thread_read" and kw["thread_id"] == "D1" and kw["include_turns"] is False
        for name, kw in clients[0].calls
    )
    assert ctx.client is clients[0]
    first_provider_session_id = ctx.provider_session_id
    assert first_provider_session_id

    # Simulate app-server crash (stdout EOF → wait_closed returns).
    clients[0].closed.set()
    await asyncio.sleep(0.05)

    # Supervisor started a fresh client and restored lanes on it.
    assert len(clients) == 2
    assert clients[1].resumed == ["O1"]
    assert any(
        name == "thread_resume" and kw["permission_profile"] == ":read-only"
        for name, kw in clients[1].calls
    )
    assert any(
        name == "thread_read" and kw["thread_id"] == "D1" and kw["include_turns"] is False
        for name, kw in clients[1].calls
    )
    assert ctx.client is clients[1]
    assert ctx.provider_session_id
    assert ctx.provider_session_id != first_provider_session_id

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_recovers_and_drains_idle_queue_on_start(store: Registry) -> None:
    await store.add_lane(id="O1", handle="@own", source="own", status="idle")
    queued = await store.enqueue_message(lane="O1", text="resume queued")
    assert await store.claim_queued_message(queued.id)
    ctx = make_ctx(store)
    clients: list[FakeSupervisedClient] = []

    async def make_client() -> FakeSupervisedClient:
        client = FakeSupervisedClient()
        clients.append(client)
        return client

    runner = TriggerRunner(ctx, lambda: _T0)
    supervisor = Supervisor(ctx, make_client, lambda: Reactor(ctx, runner).run(), backoff=0)

    first = await make_client()
    task = asyncio.create_task(supervisor.supervise(first))
    await asyncio.sleep(0.05)

    assert (await store.get_queued_message(queued.id)).status == "sent"
    assert any(
        name == "turn_start" and kw["text"] == "resume queued" for name, kw in clients[0].calls
    )

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_revalidates_profile_and_fails_closed_on_older_binary(
    store: Registry,
) -> None:
    await store.add_lane(id="O1", handle="@own", source="own", cwd="/work", status="idle")
    await store.upsert_lane_runtime_settings(
        runtime_settings_for_lane(
            lane="O1",
            updated_at="2026-06-03T12:00:00+00:00",
            permission_profile=":read-only",
        )
    )

    class OlderClient(FakeSupervisedClient):
        async def permission_profile_list(
            self, *, cwd: str | None = None, limit: int | None = None
        ) -> list[PermissionProfileSummary]:
            raise AppServerError(-32601, "method not found")

    ctx = make_ctx(store)

    async def make_client() -> OlderClient:
        return OlderClient()

    runner = TriggerRunner(ctx, lambda: _T0)
    supervisor = Supervisor(ctx, make_client, lambda: Reactor(ctx, runner).run(), backoff=0)
    client = await make_client()
    task = asyncio.create_task(supervisor.supervise(client))
    await asyncio.sleep(0.05)

    assert client.resumed == []
    assert (await store.get_lane("O1")).status == "error"
    assert not any(name == "thread_resume" for name, _ in client.calls)

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_starts_reactor_before_restoring_lanes(store: Registry) -> None:
    await store.add_lane(id="O1", handle="@own", source="own", status="idle")
    ctx = make_ctx(store)
    reactor_started = asyncio.Event()
    resume_observations: list[bool] = []

    class OrderedClient(FakeSupervisedClient):
        async def thread_resume(
            self,
            thread_id: str,
            *,
            permission_profile: str | None = None,
            exclude_turns: bool | None = None,
            initial_turns_page: ThreadResumeInitialTurnsPageParams | None = None,
        ) -> ThreadInfo:
            resume_observations.append(reactor_started.is_set())
            return await super().thread_resume(
                thread_id,
                permission_profile=permission_profile,
                exclude_turns=exclude_turns,
                initial_turns_page=initial_turns_page,
            )

    async def make_client() -> OrderedClient:
        return OrderedClient()

    async def run_reactor() -> None:
        reactor_started.set()
        await asyncio.Event().wait()

    supervisor = Supervisor(ctx, make_client, run_reactor, backoff=0)
    first = await make_client()
    task = asyncio.create_task(supervisor.supervise(first))
    await asyncio.sleep(0.05)

    assert resume_observations == [True]
    assert any(name == "thread_resume" and kw["exclude_turns"] is True for name, kw in first.calls)

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_falls_back_when_legacy_resume_rejects_exclude_turns(
    store: Registry,
) -> None:
    await store.add_lane(id="O1", handle="@own", source="own", status="idle")
    ctx = make_ctx(store)

    class LegacyClient(FakeSupervisedClient):
        async def thread_resume(
            self, thread_id: str, *, exclude_turns: bool | None = None, **kwargs: object
        ) -> ThreadInfo:
            self._record("legacy_thread_resume", thread_id=thread_id, exclude_turns=exclude_turns)
            if exclude_turns is True:
                raise AppServerError(-32602, "unknown field excludeTurns")
            return ThreadInfo(id=thread_id)

    async def make_client() -> LegacyClient:
        return LegacyClient()

    async def run_reactor() -> None:
        await asyncio.Event().wait()

    supervisor = Supervisor(ctx, make_client, run_reactor, backoff=0)
    first = await make_client()
    task = asyncio.create_task(supervisor.supervise(first))
    await asyncio.sleep(0.05)

    calls = [kw["exclude_turns"] for name, kw in first.calls if name == "legacy_thread_resume"]
    assert calls == [True, None]

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_restores_explicitly_synced_attached_observation(
    store: Registry,
) -> None:
    await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")
    await store.upsert_lane_sync(
        LaneSync(
            lane="D1",
            state="partial",
            history_capability="unsupported",
            observation_enabled=True,
        )
    )
    ctx = make_ctx(store)

    async def make_client() -> FakeSupervisedClient:
        return FakeSupervisedClient()

    async def run_reactor() -> None:
        await asyncio.Event().wait()

    supervisor = Supervisor(ctx, make_client, run_reactor, backoff=0)
    first = await make_client()
    task = asyncio.create_task(supervisor.supervise(first))
    await asyncio.sleep(0.05)

    assert first.resumed == ["D1"]
    assert not any(name == "thread_read" for name, _ in first.calls)

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)
