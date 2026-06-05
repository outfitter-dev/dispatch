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

from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.events import LaneEvent, TurnCompleted

from ._drive import run_turn, run_turn_autoapprove

pytestmark = pytest.mark.integration


async def test_read_only_turn_returns_pong(client: AppServerClient, work_dir: Path) -> None:
    thread = await client.thread_start(cwd=str(work_dir), sandbox="read-only", ephemeral=True)
    text = await run_turn(client, thread.id, "Reply with exactly one word: pong", str(work_dir))
    assert "pong" in text.lower()


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
