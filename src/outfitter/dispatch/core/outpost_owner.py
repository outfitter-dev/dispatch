"""One exclusively owned Claude stream process; an uncertain write is never replayed."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path

from outfitter.dispatch.core.outpost_models import ClaudeResult, OutpostRunnerError

STOP_TIMEOUT_SECONDS = 5.0


class ClaudeOwner:
    def __init__(self, session_id: str, process: asyncio.subprocess.Process) -> None:
        self.session_id = session_id
        self.process = process
        self.pending = False
        self.stream_failed = False
        self._results: asyncio.Queue[ClaudeResult | None] = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def start(
        cls, session_id: str, cwd: Path, config: Path, model: str | None, token: str
    ) -> ClaudeOwner:
        argv: Sequence[str] = [
            "claude",
            "-p",
            "--no-session-persistence",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--session-id",
            session_id,
            "--mcp-config",
            str(config.absolute()),
            "--strict-mcp-config",
            "--tools",
            "",
            "--allowedTools",
            "mcp__Outpost__outpost_whoami",
            "mcp__Outpost__outpost_inbox_list",
            "mcp__Outpost__outpost_thread_get",
            "mcp__Outpost__outpost_thread_create",
            "mcp__Outpost__outpost_thread_reply",
            "--setting-sources",
            "",
        ]
        if model:
            argv = [*argv, "--model", model]
        # Child shell tools are unavailable. Existing provider authentication remains inherited.
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env={**os.environ, "OUTPOST_TOKEN": token},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
        )
        return cls(session_id, process)

    @property
    def running(self) -> bool:
        return self.process.returncode is None and not self.stream_failed

    async def _read(self) -> None:
        assert self.process.stdout
        stream = self.process.stdout
        discarding = False
        try:
            while True:
                try:
                    raw = await stream.readuntil(b"\n")
                except asyncio.LimitOverrunError as exc:
                    await stream.readexactly(exc.consumed)
                    discarding = True
                    continue
                except asyncio.IncompleteReadError as exc:
                    if discarding or not exc.partial:
                        break
                    raw = exc.partial
                if discarding:
                    discarding = False
                    continue
                try:
                    value: object = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get("type") == "result":
                    await self._results.put(ClaudeResult.model_validate(value))
        except Exception:
            # Reader failures signal an unknown provider outcome through the queue.
            # They must not escape shutdown and mask the original submission error.
            self.stream_failed = True
        finally:
            await self._results.put(None)

    async def submit(self, text: str) -> None:
        if self.process.returncode is not None:
            raise OutpostRunnerError("Claude owner has ended")
        assert self.process.stdin
        self.pending = True
        self.process.stdin.write(
            (
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": text},
                    }
                )
                + "\n"
            ).encode()
        )
        await self.process.stdin.drain()
        result = await self._results.get()
        if result is None:
            raise OutpostRunnerError("Claude owner ended after a possible input write")
        if result.session_id != self.session_id or result.is_error:
            raise OutpostRunnerError(
                "Claude owner did not confirm a successful result for this session"
            )
        self.pending = False

    async def stop(self) -> None:
        if self.process.returncode is None:
            assert self.process.stdin
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=STOP_TIMEOUT_SECONDS)
            except TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=STOP_TIMEOUT_SECONDS)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        await self._reader
