from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

from outfitter.dispatch.core import outpost_runner
from outfitter.dispatch.core.outpost_models import Binding, OutpostRunnerError, Wake
from outfitter.dispatch.core.outpost_owner import ClaudeOwner

BINDING = Binding(agent="claude-1234", startedAt="generation-1", mcp_session="binding-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "busy,stop_error,idle_exit",
    [
        (True, False, False),
        (False, False, False),
        (False, True, False),
        (False, False, True),
    ],
)
async def test_deadline_distinguishes_inflight_input_from_idle_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    busy: bool,
    stop_error: bool,
    idle_exit: bool,
) -> None:
    closed = False

    class Feed:
        mcp_url = "https://test/mcp"
        api_url = "https://test"

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
        stream_failed = False

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
            if idle_exit:
                self.running = False
            if busy:
                await asyncio.sleep(60)

        async def stop(self) -> None:
            self.running = False
            if stop_error:
                raise OutpostRunnerError("synthetic reader failure")

    monkeypatch.setattr(outpost_runner, "OutpostFeed", Feed)
    monkeypatch.setattr(outpost_runner, "ClaudeOwner", Owner)
    reports: list[str] = []
    if busy:
        with pytest.raises(OutpostRunnerError, match="indeterminate Claude input"):
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
    elif idle_exit:
        with pytest.raises(OutpostRunnerError, match="owner ended before"):
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
        with pytest.raises(OutpostRunnerError, match="synthetic reader failure"):
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


@pytest.mark.asyncio
async def test_oversized_event_is_discarded_and_terminal_result_is_still_observed() -> None:
    script = (
        "import json,sys;sys.stdin.readline();"
        "print(json.dumps({'type':'user','content':'x'*4096}),flush=True);"
        "print(json.dumps({'type':'result','session_id':'fixture','is_error':False}),flush=True);"
        "sys.stdin.read()"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        limit=1024,
    )
    owner = ClaudeOwner("fixture", process)
    try:
        await asyncio.wait_for(owner.submit("synthetic"), timeout=3)
        assert not owner.pending
        assert not owner.stream_failed
    finally:
        await owner.stop()


@pytest.mark.asyncio
async def test_relative_configuration_is_absolute_before_changing_child_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = asyncio.create_subprocess_exec
    captured: tuple[str, ...] = ()

    async def launch(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        nonlocal captured
        captured = argv
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["OUTPOST_TOKEN"] == "fixture-token"
        return await original(
            sys.executable,
            "-c",
            "import sys;sys.stdin.read()",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    relative = Path("relative-run/mcp.json")
    owner = await ClaudeOwner.start("fixture", tmp_path, relative, None, "fixture-token")
    try:
        assert captured[captured.index("--mcp-config") + 1] == str(relative.absolute())
        assert "fixture-token" not in captured
    finally:
        await owner.stop()
