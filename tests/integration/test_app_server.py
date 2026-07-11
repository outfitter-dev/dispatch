"""Integration tests against a REAL ephemeral codex app-server.

Verifies the primitives dispatch is built on (PLAN Phase 1): initialize, a
read-only turn answering ``pong``, inject_items recall, thread/list reading
``result.data``, approval-accept resuming a write turn, and same-connection
persisted-resume yielding live events. Auto-skips when codex/auth are
unavailable (see conftest).
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import structlog

from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.events import LaneEvent, TurnCompleted
from outfitter.dispatch.client.models import SandboxPolicy
from outfitter.dispatch.config import RuntimePolicy
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.core.backfill import backfill_codex_history
from outfitter.dispatch.core.capacity import refresh_codex_capacity
from outfitter.dispatch.core.server_requests import ServerRequestManager, respond_to_server_request
from outfitter.dispatch.registry.store import Registry

from ._drive import run_turn, run_turn_autoapprove

pytestmark = pytest.mark.integration


async def test_account_capacity_probe_prints_only_redacted_observation(
    client: AppServerClient,
) -> None:
    account = await client.account_read()
    registry = await Registry.open()
    ctx = Ctx(
        client=client,
        registry=registry,
        log=structlog.get_logger(),
        abort=asyncio.Event(),
        policy=RuntimePolicy(),
        provider_session_id="integration-app-server",
    )
    try:
        observation = await refresh_codex_capacity(ctx)
        payload = observation.model_dump_json()
        print(payload)
        assert "accessToken" not in payload
        assert "refreshToken" not in payload
        if account.account is not None and account.account.email is not None:
            assert account.account.email not in payload
            assert observation.account_label != account.account.email
    finally:
        await registry.close()


async def test_read_only_turn_returns_pong(client: AppServerClient, work_dir: Path) -> None:
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=True)
    text = await run_turn(client, thread.id, "Reply with exactly one word: pong", str(work_dir))
    assert "pong" in text.lower()


async def test_permission_profiles_list_and_apply_without_a_turn(
    client: AppServerClient, work_dir: Path
) -> None:
    profiles = await client.permission_profile_list(cwd=str(work_dir), limit=1)
    allowed = {profile.id for profile in profiles if profile.allowed}
    assert ":read-only" in allowed

    thread = await client.thread_start(
        cwd=str(work_dir), permission_profile=":read-only", ephemeral=True
    )
    assert thread.id


async def test_inject_items_then_recall(client: AppServerClient, work_dir: Path) -> None:
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=True)
    await client.inject_items(
        thread.id,
        [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "REMEMBER: the codeword is BANANA."}],
            }
        ],
    )
    text = await run_turn(
        client,
        thread.id,
        "What codeword did I tell you earlier? Reply with one word.",
        str(work_dir),
    )
    assert "banana" in text.lower()


async def test_thread_list_reads_data_key(client: AppServerClient, work_dir: Path) -> None:
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=False)
    # A thread persists (becomes listable + archivable) only after a turn completes.
    await run_turn(client, thread.id, "Reply with exactly one word: ok", str(work_dir))
    try:
        threads = await client.thread_list(limit=100, use_state_db_only=True)
        assert any(t.id == thread.id for t in threads)  # parsed from result.data
    finally:
        await client.thread_archive(thread.id)


async def test_approval_accept_resumes_write_turn(client: AppServerClient, work_dir: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=work_dir, check=True)
    thread = await client.thread_start(
        cwd=str(work_dir), sandbox="workspace-write", approval_policy="untrusted", ephemeral=True
    )
    await run_turn_autoapprove(
        client,
        thread.id,
        "Create a file named notes.txt whose entire contents are exactly: HELLO",
        str(work_dir),
    )
    notes = work_dir / "notes.txt"
    assert notes.exists(), "approval-accepted write turn did not create the file"
    assert "HELLO" in notes.read_text()


async def test_dispatch_request_manager_completes_real_approval(
    client: AppServerClient, work_dir: Path
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=work_dir, check=True)
    thread = await client.thread_start(
        cwd=str(work_dir), sandbox="workspace-write", approval_policy="untrusted", ephemeral=True
    )
    registry = await Registry.open()
    await registry.add_lane(id=thread.id, handle="@approval-probe", source="own", status="idle")
    ctx = Ctx(
        client=client,
        registry=registry,
        log=structlog.get_logger(),
        abort=asyncio.Event(),
        policy=RuntimePolicy(owned_interactive_requests="permissive"),
        provider_session_id="integration-app-server",
    )
    manager = ServerRequestManager(ctx)
    manager_task = asyncio.create_task(manager.run())
    await asyncio.sleep(0)  # register the eager generic request subscription
    raw = client.raw_events(thread.id)
    try:
        await client.turn_start(
            thread.id,
            "Create a file named managed.txt whose entire contents are exactly: MANAGED",
            str(work_dir),
            approval_policy="untrusted",
            sandbox_policy=SandboxPolicy(type="workspaceWrite"),
            effort="low",
        )
        async with asyncio.timeout(200):
            async for message in raw:
                if message.get("method") == "turn/completed":
                    break
        requests = await registry.list_server_requests(state=None)
        assert requests
        assert {request.state for request in requests} == {"responded"}
        assert (work_dir / "managed.txt").read_text().strip() == "MANAGED"
    finally:
        manager_task.cancel()
        await asyncio.gather(manager_task, return_exceptions=True)
        await registry.close()


async def test_dispatch_request_manager_completes_plan_mode_user_input(
    client: AppServerClient, work_dir: Path
) -> None:
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=True)
    config = await client.config_read()
    await client._request(  # test-only experimental setup; the request path is under test elsewhere
        "thread/settings/update",
        {
            "threadId": thread.id,
            "collaborationMode": {
                "mode": "plan",
                "settings": {
                    "model": config.model or "gpt-5.5",
                    "reasoning_effort": "low",
                },
            },
        },
    )
    registry = await Registry.open()
    await registry.add_lane(id=thread.id, handle="@input-probe", source="own", status="idle")
    ctx = Ctx(
        client=client,
        registry=registry,
        log=structlog.get_logger(),
        abort=asyncio.Event(),
        provider_session_id="integration-app-server",
    )
    manager_task = asyncio.create_task(ServerRequestManager(ctx).run())
    await asyncio.sleep(0)
    raw = client.raw_events(thread.id)

    async def answer_request() -> int:
        async with asyncio.timeout(90):
            while True:
                requests = await registry.list_server_requests(state="pending", lane=thread.id)
                if requests:
                    request = requests[0]
                    assert request.category == "user_input"
                    assert request.id is not None
                    await respond_to_server_request(
                        ctx, request.id, {"answers": {"color": {"answers": ["blue"]}}}
                    )
                    return request.id
                await asyncio.sleep(0.05)

    answer_task = asyncio.create_task(answer_request())
    try:
        await client.turn_start(
            thread.id,
            (
                "Use request_user_input to ask id color with options red and blue. "
                "After the answer, reply with the selected color only."
            ),
            str(work_dir),
            effort="low",
        )
        async with asyncio.timeout(150):
            async for message in raw:
                if message.get("method") == "turn/completed":
                    break
        request_id = await answer_task
        completed = await registry.get_server_request_by_id(request_id)
        assert completed is not None
        assert completed.state == "responded"
    finally:
        answer_task.cancel()
        manager_task.cancel()
        await asyncio.gather(answer_task, manager_task, return_exceptions=True)
        await registry.close()


async def test_dispatch_request_manager_completes_real_mcp_elicitation(
    elicitation_client: AppServerClient, work_dir: Path
) -> None:
    client = elicitation_client
    thread = await client.thread_start(
        cwd=str(work_dir),
        sandbox="read-only",
        developer_instructions=(
            "When asked to choose a color, call the dispatch_elicitation_probe ask_color tool."
        ),
        ephemeral=True,
    )
    registry = await Registry.open()
    await registry.add_lane(id=thread.id, handle="@elicitation-probe", source="own")
    ctx = Ctx(
        client=client,
        registry=registry,
        log=structlog.get_logger(),
        abort=asyncio.Event(),
        provider_session_id="integration-app-server",
    )
    manager_task = asyncio.create_task(ServerRequestManager(ctx).run())
    await asyncio.sleep(0)
    raw = client.raw_events(thread.id)

    async def answer_elicitation() -> int:
        async with asyncio.timeout(90):
            while True:
                requests = await registry.list_server_requests(state="pending", lane=thread.id)
                if requests:
                    request = requests[0]
                    assert request.category == "elicitation"
                    assert request.id is not None
                    await respond_to_server_request(
                        ctx,
                        request.id,
                        {"action": "accept", "content": {"color": "blue"}},
                    )
                    return request.id
                await asyncio.sleep(0.05)

    answer_task = asyncio.create_task(answer_elicitation())
    try:
        await client.turn_start(
            thread.id,
            "Call the color-choice tool, then reply with its selected color only.",
            str(work_dir),
            effort="low",
        )
        async with asyncio.timeout(150):
            async for message in raw:
                if message.get("method") == "turn/completed":
                    break
        request_id = await answer_task
        completed = await registry.get_server_request_by_id(request_id)
        assert completed is not None
        assert completed.state == "responded"
    finally:
        answer_task.cancel()
        manager_task.cancel()
        await asyncio.gather(answer_task, manager_task, return_exceptions=True)
        await registry.close()


async def test_persisted_resume_yields_live_events(client: AppServerClient, work_dir: Path) -> None:
    # dispatch's real topology: ONE app-server, many lanes on one connection. Resuming
    # a persisted thread on that connection yields live events for the next turn.
    # (Cross-PROCESS live fan-out does NOT happen — recorded in RETRO/ADR-0005 from the
    # Phase-1 spike; that is why attached lanes stay turn-write locked.)
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=False)
    await run_turn(client, thread.id, "Reply one word: alpha", str(work_dir))  # persist a rollout
    resumed = await client.thread_resume(thread.id)
    assert resumed.id == thread.id
    events = client.events(thread.id)
    saw_completion = asyncio.create_task(_await_completion(events, thread.id))
    await run_turn(client, thread.id, "Reply with exactly one word: omega", str(work_dir))
    try:
        assert await asyncio.wait_for(saw_completion, timeout=60)
    finally:
        await client.thread_archive(thread.id)


async def test_bounded_resume_bootstraps_recent_turn_then_items(
    client: AppServerClient, work_dir: Path
) -> None:
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=False)
    registry = await Registry.open()
    try:
        await run_turn(client, thread.id, "Reply with exactly one word: bounded", str(work_dir))
        lane = await registry.add_lane(
            id=thread.id, handle="@bounded-resume", source="own", status="idle"
        )
        result = await backfill_codex_history(
            client=client,
            registry=registry,
            lane=lane,
            max_turns=2,
            max_items=20,
            max_seconds=10,
            max_bytes=262_144,
        )
        assert result.capability in {"supported", "turn-page-fallback"}
        assert result.pages_scanned >= 1
        assert result.turns_indexed >= 1
        assert result.items_indexed >= 1
        assert await registry.list_thread_items(lane=thread.id)
    finally:
        await registry.close()
        await client.thread_archive(thread.id)


async def test_thread_read_goal_and_history_controls(
    client: AppServerClient, work_dir: Path
) -> None:
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=False)
    try:
        await run_turn(client, thread.id, "Reply with exactly one word: alpha", str(work_dir))
        read = await client.thread_read(thread.id, include_turns=True)
        payload = read.get("thread")
        assert isinstance(payload, dict)
        turns = payload.get("turns")
        assert isinstance(turns, list)
        assert turns, "includeTurns did not populate persisted turns"

        goal = await client.thread_goal_set(thread.id, objective="Finish the integration probe.")
        assert goal.objective == "Finish the integration probe."
        assert goal.status == "active"
        assert (await client.thread_goal_get(thread.id)) is not None
        await client.thread_goal_clear(thread.id)
        assert await client.thread_goal_get(thread.id) is None

        fork = await client.thread_fork(thread.id, cwd=str(work_dir), ephemeral=True)
        assert fork.id != thread.id
        assert fork.forked_from_id == thread.id

        await run_turn(client, thread.id, "Reply with exactly one word: beta", str(work_dir))
        before = await client.thread_read(thread.id, include_turns=True)
        before_thread = before.get("thread")
        before_turns = before_thread.get("turns") if isinstance(before_thread, dict) else None
        assert isinstance(before_turns, list)
        await client.thread_rollback(thread.id, 1)
        after = await client.thread_read(thread.id, include_turns=True)
        after_thread = after.get("thread")
        after_turns = after_thread.get("turns") if isinstance(after_thread, dict) else None
        assert isinstance(after_turns, list)
        assert len(after_turns) < len(before_turns)

        await client.thread_compact_start(thread.id)
    finally:
        await client.thread_archive(thread.id)


async def test_thread_fork_is_not_a_spawned_descendant(
    client: AppServerClient, work_dir: Path
) -> None:
    root = await client.thread_start(
        cwd=str(work_dir), sandbox="read-only", model="gpt-5.3-codex-spark", ephemeral=False
    )
    fork = None
    try:
        await run_turn(client, root.id, "Reply with exactly one word: root", str(work_dir))
        fork = await client.thread_fork(root.id, cwd=str(work_dir), ephemeral=False)
        await run_turn(client, fork.id, "Reply with exactly one word: fork", str(work_dir))

        direct = await client.thread_list(
            limit=20,
            parent_thread_id=root.id,
            use_state_db_only=True,
        )
        descendants = await client.thread_list(
            limit=20,
            ancestor_thread_id=root.id,
            use_state_db_only=True,
        )

        assert fork.forked_from_id == root.id
        assert fork.parent_thread_id is None
        assert all(thread.id != fork.id for thread in direct)
        assert all(thread.id != fork.id for thread in descendants)
    finally:
        if fork is not None:
            await client.thread_archive(fork.id)
        await client.thread_archive(root.id)


async def test_thread_search_and_unarchive_primitives(
    client: AppServerClient, work_dir: Path
) -> None:
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=False)
    try:
        await run_turn(
            client,
            thread.id,
            "Reply with exactly one word: dispatchsearchneedle",
            str(work_dir),
        )
        assert await _await_search_match(client, "dispatchsearchneedle", thread.id)

        await client.thread_archive(thread.id)
        restored = await client.thread_unarchive(thread.id)
        assert restored.id == thread.id
    finally:
        await client.thread_archive(thread.id)


async def _await_completion(events: AsyncIterator[LaneEvent], lane: str) -> bool:
    async for event in events:
        if isinstance(event, TurnCompleted) and event.lane_id == lane:
            return True
    return False


async def _await_search_match(client: AppServerClient, query: str, thread_id: str) -> bool:
    for _ in range(10):
        result = await client.thread_search(query, limit=20)
        if any(match.thread.id == thread_id for match in result.data):
            return True
        await asyncio.sleep(0.25)
    return False
