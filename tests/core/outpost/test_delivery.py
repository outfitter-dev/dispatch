from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from outfitter.dispatch.core.outpost_feed import Binding, OutpostFeed, Wake
from outfitter.dispatch.core.outpost_runner import RunLedger, deliver_wakes

BINDING = Binding(agent="claude-1234", startedAt="generation-1", mcp_session="binding-1")
WAKE = Wake(
    id="response-1",
    agent=BINDING.agent,
    startedAt=BINDING.started_at,
    thread="ask-1",
    message="reply-1",
    channel="test",
)


class Owner:
    running = True

    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[str] = []
        self.fail = fail

    async def submit(self, text: str) -> None:
        self.messages.append(text)
        if self.fail:
            raise ValueError("owner ended after a possible write")


@pytest.mark.asyncio
async def test_repeated_feed_and_lost_ack_never_resubmit_completed_wake(tmp_path: Path) -> None:
    owner = Owner()
    ack_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ack_count
        assert request.headers["outpost-act-as"] == BINDING.agent
        if request.method == "POST":
            ack_count += 1
            if ack_count == 1:
                return httpx.Response(503)
            owner.running = False
            return httpx.Response(200, json={"acknowledged": True})
        return httpx.Response(
            200,
            json={
                "status": "active",
                "wakes": [
                    WAKE.model_dump(by_alias=True),
                ],
            },
        )

    ledger = RunLedger(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await deliver_wakes(
            OutpostFeed(client, "https://test", "https://test/mcp", "token"),
            BINDING,
            owner,
            ledger,
            tmp_path,
            lambda message: None,
        )
    assert len(owner.messages) == 1
    assert ack_count == 2
    assert ledger.state(WAKE.id) == "completed"


@pytest.mark.asyncio
async def test_possible_input_loss_stays_indeterminate_without_ack(tmp_path: Path) -> None:
    owner = Owner(fail=True)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "status": "active",
                "wakes": [
                    WAKE.model_dump(by_alias=True),
                ],
            },
        )

    ledger = RunLedger(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        feed = OutpostFeed(client, "https://test", "https://test/mcp", "token")
        with pytest.raises(ValueError, match="possible write"):
            await deliver_wakes(feed, BINDING, owner, ledger, tmp_path, lambda message: None)
        with pytest.raises(ValueError, match="indeterminate"):
            await deliver_wakes(feed, BINDING, owner, ledger, tmp_path, lambda message: None)
    assert len(owner.messages) == 1
    assert ledger.state(WAKE.id) == "indeterminate"


@pytest.mark.asyncio
async def test_local_pause_retains_response_until_resume(tmp_path: Path) -> None:
    paused = tmp_path / "paused"
    paused.touch()
    owner = Owner()
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.method == "POST":
            owner.running = False
            return httpx.Response(200, json={"acknowledged": True})
        polls += 1
        if polls == 2:
            assert owner.messages == []
            paused.unlink()
        return httpx.Response(
            200,
            json={
                "status": "active",
                "wakes": [
                    WAKE.model_dump(by_alias=True),
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await deliver_wakes(
            OutpostFeed(client, "https://test", "https://test/mcp", "token"),
            BINDING,
            owner,
            RunLedger(tmp_path),
            tmp_path,
            lambda message: None,
        )
    assert polls >= 2
    assert len(owner.messages) == 1


@pytest.mark.asyncio
async def test_pause_during_reservation_waits_for_resume_without_ending_owner(
    tmp_path: Path,
) -> None:
    import asyncio

    owner = Owner()
    paused = tmp_path / "paused"

    class PausingLedger(RunLedger):
        def reserve(self, wake: Wake) -> None:
            super().reserve(wake)
            paused.touch()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            owner.running = False
            return httpx.Response(200, json={"acknowledged": True})
        return httpx.Response(
            200,
            json={
                "status": "active",
                "wakes": [
                    WAKE.model_dump(by_alias=True),
                ],
            },
        )

    async def resume() -> None:
        while not paused.exists():
            await asyncio.sleep(0.01)
        assert owner.messages == []
        paused.unlink()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await asyncio.gather(
            deliver_wakes(
                OutpostFeed(client, "https://test", "https://test/mcp", "token"),
                BINDING,
                owner,
                PausingLedger(tmp_path),
                tmp_path,
                lambda message: None,
            ),
            resume(),
        )
    assert len(owner.messages) == 1


@pytest.mark.asyncio
async def test_ended_binding_after_held_pause_never_submits_cached_wake(tmp_path: Path) -> None:
    import asyncio

    paused = tmp_path / "paused"
    owner = Owner()
    ended = False

    class PausingLedger(RunLedger):
        def reserve(self, wake: Wake) -> None:
            super().reserve(wake)
            paused.touch()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "status": "ended" if ended else "active",
                "wakes": [] if ended else [WAKE.model_dump(by_alias=True)],
            },
        )

    async def resume_after_end() -> None:
        nonlocal ended
        while not paused.exists():
            await asyncio.sleep(0.01)
        ended = True
        paused.unlink()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="recipient has ended"):
            await asyncio.gather(
                deliver_wakes(
                    OutpostFeed(client, "https://test", "https://test/mcp", "token"),
                    BINDING,
                    owner,
                    PausingLedger(tmp_path),
                    tmp_path,
                    lambda message: None,
                ),
                resume_after_end(),
            )
    assert owner.messages == []


@pytest.mark.asyncio
async def test_refresh_outage_before_input_retries_without_losing_reserved_wake(
    tmp_path: Path,
) -> None:
    owner = Owner()
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.method == "POST":
            owner.running = False
            return httpx.Response(200, json={"acknowledged": True})
        polls += 1
        if polls == 2:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "status": "active",
                "wakes": [
                    WAKE.model_dump(by_alias=True),
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await deliver_wakes(
            OutpostFeed(client, "https://test", "https://test/mcp", "token"),
            BINDING,
            owner,
            RunLedger(tmp_path),
            tmp_path,
            lambda message: None,
        )
    assert len(owner.messages) == 1
    assert polls == 3
