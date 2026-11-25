from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

from outfitter.dispatch.core import outpost_runner
from outfitter.dispatch.core.outpost_feed import Binding, Wake
from outfitter.dispatch.core.outpost_owner import ClaudeOwner

BINDING = Binding(agent="claude-1234", startedAt="generation-1", mcp_session="binding-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("busy,stop_error", [(True, False), (False, False), (False, True)])
async def test_deadline_distinguishes_inflight_input_from_idle_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    busy: bool,
    stop_error: bool,
) -> None:
    closed = False

    class Feed:
        mcp_url = "https://test/mcp"

        def __init__(self, client: httpx.AsyncClient, api: str, mcp: str, token: str) -> None:
            pass

        async def bind(self) -> Binding:
            return BINDING

        async def pending(self, binding: Binding) -> list[Wake]:
            return []

        async def close(self) -> None:
            nonlocal closed
            closed = True

    class Owner:
        running = True
        pending = False

        @classmethod
        async def start(
            cls,
            session_id: str,
            cwd: Path,
            config: Path,
            model: str | None,
            token: str,
        ) -> Owner:
            return cls()

        async def submit(self, text: str) -> None:
            self.pending = busy
            if busy:
                await asyncio.sleep(60)

        async def stop(self) -> None:
            self.running = False
            if stop_error:
                raise ValueError("synthetic reader failure")

    monkeypatch.setattr(outpost_runner, "OutpostFeed", Feed)
    monkeypatch.setattr(outpost_runner, "ClaudeOwner", Owner)
    reports: list[str] = []
    if busy:
        with pytest.raises(ValueError, match="indeterminate Claude input"):
            await outpost_runner.run_outpost_owner(
                api_url="https://test",
                mcp_url="https://test/mcp",
                run_dir=tmp_path / "run",
                cwd=tmp_path,
                prompt="synthetic",
                token="fixture",
                model=None,
                max_seconds=0.01,
                report=reports.append,
            )
    elif stop_error:
        with pytest.raises(ValueError, match="synthetic reader failure"):
            await outpost_runner.run_outpost_owner(
                api_url="https://test",
                mcp_url="https://test/mcp",
                run_dir=tmp_path / "run",
                cwd=tmp_path,
                prompt="synthetic",
                token="fixture",
                model=None,
                max_seconds=0.01,
                report=reports.append,
            )
    else:
        await outpost_runner.run_outpost_owner(
            api_url="https://test",
            mcp_url="https://test/mcp",
            run_dir=tmp_path / "run",
            cwd=tmp_path,
            prompt="synthetic",
            token="fixture",
            model=None,
            max_seconds=0.01,
            report=reports.append,
        )
        assert reports[-1] == "run window ended"
    assert closed


@pytest.mark.asyncio
async def test_shutdown_kills_a_child_that_ignores_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from outfitter.dispatch.core import outpost_owner

    monkeypatch.setattr(outpost_owner, "STOP_TIMEOUT_SECONDS", 0.02)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        (
            "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "print('ready',flush=True);time.sleep(60)"
        ),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout
    assert await process.stdout.readline() == b"ready\n"
    owner = ClaudeOwner("fixture", process)
    await asyncio.wait_for(owner.stop(), timeout=2)
    assert process.returncode is not None
