"""Transport for the App Server: spawn ``codex app-server --listen stdio://`` and
move newline-delimited JSON over its stdio (the only bare-JSONL transport).

The client depends on the ``Transport`` protocol, not on the concrete subprocess
implementation, so it is unit-testable with an in-memory fake. ``receive()``
returns ``None`` on stdout EOF — the daemon reads that as an app-server crash and
restarts (Phase 5).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Protocol

from .errors import ProtocolError, TransportError


class Transport(Protocol):
    """Bidirectional JSON message channel to one app-server connection."""

    async def send(self, message: Mapping[str, object]) -> None: ...

    async def receive(self) -> dict[str, object] | None:
        """Next message, or ``None`` at end of stream (EOF/crash)."""
        ...

    async def close(self) -> None: ...


class StdioTransport:
    """Concrete ``Transport`` over a ``codex app-server`` stdio subprocess."""

    def __init__(
        self,
        argv: Sequence[str] = ("codex", "app-server", "--listen", "stdio://"),
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._argv = tuple(argv)
        self._env = dict(env) if env is not None else None
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_tail: deque[str] = deque(maxlen=50)
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
        except OSError as exc:  # binary missing / not executable
            raise TransportError(f"failed to spawn {self._argv[0]}: {exc}") from exc
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    def _require_proc(self) -> asyncio.subprocess.Process:
        if self._proc is None:
            raise TransportError("transport not started")
        return self._proc

    async def send(self, message: Mapping[str, object]) -> None:
        proc = self._require_proc()
        if proc.stdin is None or proc.stdin.is_closing():
            raise TransportError("app-server stdin is closed")
        line = json.dumps(dict(message)) + "\n"
        proc.stdin.write(line.encode())
        try:
            await proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError) as exc:
            raise TransportError(f"app-server stdin write failed: {exc}") from exc

    async def receive(self) -> dict[str, object] | None:
        proc = self._require_proc()
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if raw == b"":  # EOF: process closed stdout (crashed or exited)
                return None
            text = raw.decode(errors="replace").strip()
            if not text:
                continue
            try:
                parsed: object = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProtocolError(f"non-JSON line from app-server: {text[:200]!r}") from exc
            if not isinstance(parsed, dict):
                raise ProtocolError(f"expected a JSON object, got {type(parsed).__name__}")
            return parsed

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        async for line in proc.stderr:
            self._stderr_tail.append(line.decode(errors="replace").rstrip("\n"))

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    async def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
