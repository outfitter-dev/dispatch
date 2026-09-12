"""Supervisor: restart the app-server on crash and restore lane observation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from outfitter.dispatch.client.errors import AppServerError, ClientError
from outfitter.dispatch.client.models import (
    PermissionProfileSummary,
    ThreadInfo,
    ThreadResumeInitialTurnsPageParams,
)
from outfitter.dispatch.core.turn_settings import runtime_settings_for_lane
from outfitter.dispatch.daemon.supervisor import SupervisedClient, Supervisor
from outfitter.dispatch.registry.models import LaneSync
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeSupervisedClient, make_ctx


async def _wait_forever() -> None:
    await asyncio.Event().wait()


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

    supervisor = Supervisor(ctx, make_client, _wait_forever, backoff=0)

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
    first_provider_session_id = ctx.connection_generation
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
    assert ctx.connection_generation
    assert ctx.connection_generation != first_provider_session_id

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_skips_non_default_provider_bindings(store: Registry) -> None:
    await store.add_lane(
        id="dsp_other",
        handle="@other",
        source="own",
        status="idle",
        provider="codex",
        binding_id="profile-a",
        provider_session_id="native-shared",
    )
    ctx = make_ctx(store)
    client = FakeSupervisedClient()
    supervisor = Supervisor(ctx, lambda: pytest.fail("must not respawn"), lambda: pytest.fail())

    await supervisor._restore_lanes(client)

    assert not client.calls


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

    supervisor = Supervisor(ctx, make_client, _wait_forever, backoff=0)

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

    supervisor = Supervisor(ctx, make_client, _wait_forever, backoff=0)
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


async def test_supervisor_stops_only_the_current_connection_generation(
    store: Registry,
) -> None:
    ctx = make_ctx(store)
    clients: list[FakeSupervisedClient] = []

    async def make_client() -> FakeSupervisedClient:
        client = FakeSupervisedClient()
        clients.append(client)
        return client

    async def run_reactor() -> None:
        await asyncio.Event().wait()

    supervisor = Supervisor(ctx, make_client, run_reactor, backoff=0)
    first = await make_client()
    task = asyncio.create_task(supervisor.supervise(first))
    await asyncio.sleep(0.05)
    first_generation = ctx.connection_generation
    first.closed.set()
    async with asyncio.timeout(1):
        while len(clients) < 2 or ctx.connection_generation == first_generation:
            await asyncio.sleep(0)

    current_generation = ctx.connection_generation
    await supervisor.stop(expected_generation=first_generation)
    assert clients[1].closed.is_set() is False

    await supervisor.stop(expected_generation=current_generation)
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_retries_after_provider_recovery_failure(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(store)
    clients: list[FakeSupervisedClient] = []
    recovered = asyncio.Event()

    async def make_client() -> FakeSupervisedClient:
        client = FakeSupervisedClient()
        clients.append(client)
        return client

    async def run_reactor() -> None:
        await asyncio.Event().wait()

    supervisor = Supervisor(ctx, make_client, run_reactor, backoff=0)
    restore = supervisor._restore_lanes
    attempts = 0

    async def fail_once(client: SupervisedClient) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ClientError("provider history reader failed")
        await restore(client)
        recovered.set()

    monkeypatch.setattr(supervisor, "_restore_lanes", fail_once)
    task = asyncio.create_task(supervisor.supervise())

    await asyncio.wait_for(recovered.wait(), timeout=1)
    assert len(clients) == 2
    assert clients[0].closed.is_set()
    assert ctx.client is clients[1]

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_retries_after_reactor_reader_failure(store: Registry) -> None:
    ctx = make_ctx(store)
    clients: list[FakeSupervisedClient] = []
    second_reactor_started = asyncio.Event()
    reactor_runs = 0

    async def make_client() -> FakeSupervisedClient:
        client = FakeSupervisedClient()
        clients.append(client)
        return client

    async def run_reactor() -> None:
        nonlocal reactor_runs
        reactor_runs += 1
        if reactor_runs == 1:
            raise ClientError("provider event reader failed")
        second_reactor_started.set()
        await asyncio.Event().wait()

    supervisor = Supervisor(ctx, make_client, run_reactor, backoff=0)
    task = asyncio.create_task(supervisor.supervise())

    await asyncio.wait_for(second_reactor_started.wait(), timeout=1)
    assert len(clients) == 2
    assert clients[0].closed.is_set()
    assert ctx.client is clients[1]

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_retries_after_reactor_reader_exits_cleanly(
    store: Registry,
) -> None:
    ctx = make_ctx(store)
    clients: list[FakeSupervisedClient] = []
    second_reactor_started = asyncio.Event()
    reactor_runs = 0

    async def make_client() -> FakeSupervisedClient:
        client = FakeSupervisedClient()
        clients.append(client)
        return client

    async def run_reactor() -> None:
        nonlocal reactor_runs
        reactor_runs += 1
        if reactor_runs == 1:
            return
        second_reactor_started.set()
        await asyncio.Event().wait()

    supervisor = Supervisor(ctx, make_client, run_reactor, backoff=0)
    task = asyncio.create_task(supervisor.supervise())

    await asyncio.wait_for(second_reactor_started.wait(), timeout=1)
    assert len(clients) == 2
    assert clients[0].closed.is_set()
    assert ctx.client is clients[1]

    await supervisor.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_supervisor_closes_client_returned_after_shutdown(store: Registry) -> None:
    ctx = make_ctx(store)
    make_started = asyncio.Event()
    release_client = asyncio.Event()
    late_client = FakeSupervisedClient()

    async def make_client() -> FakeSupervisedClient:
        make_started.set()
        await release_client.wait()
        return late_client

    async def run_reactor() -> None:
        await asyncio.Event().wait()

    supervisor = Supervisor(ctx, make_client, run_reactor, backoff=0)
    task = asyncio.create_task(supervisor.supervise())
    await asyncio.wait_for(make_started.wait(), timeout=1)

    await supervisor.stop()
    release_client.set()
    await asyncio.wait_for(task, timeout=1)

    assert late_client.closed.is_set()
    assert ctx.client is not late_client
