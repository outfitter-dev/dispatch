"""Shared test fakes — a ``FakeLaneClient`` satisfying the ``LaneClient`` protocol.

Records calls and returns canned values so handlers can be unit-tested without a
real app-server (ADR-0006). mypy enforces it matches the protocol.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import structlog

from outfitter.dispatch.client.events import LaneEvent
from outfitter.dispatch.client.models import (
    ApprovalPolicy,
    Decision,
    Effort,
    SandboxPolicy,
    ThreadInfo,
    ThreadSandbox,
)
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.registry.store import Registry


async def _aiter[T](items: list[T]) -> AsyncIterator[T]:
    for item in items:
        yield item


class FakeLaneClient:
    """An in-memory ``LaneClient`` that records calls and returns canned values."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.next_thread_id = "lane-1"
        self.threads: dict[str, ThreadInfo] = {}
        self.list_result: list[ThreadInfo] = []
        self.read_result: dict[str, object] = {}
        self.event_log: list[LaneEvent] = []
        self.raw_log: list[dict[str, object]] = []

    def _record(self, name: str, **kwargs: object) -> None:
        self.calls.append((name, kwargs))

    async def thread_start(
        self,
        cwd: str,
        sandbox: ThreadSandbox = "read-only",
        approval_policy: ApprovalPolicy = "never",
        ephemeral: bool = False,
    ) -> ThreadInfo:
        self._record(
            "thread_start",
            cwd=cwd,
            sandbox=sandbox,
            approval_policy=approval_policy,
            ephemeral=ephemeral,
        )
        thread = ThreadInfo(id=self.next_thread_id, ephemeral=ephemeral, cwd=cwd)
        self.threads[thread.id] = thread
        return thread

    async def thread_resume(self, thread_id: str) -> ThreadInfo:
        self._record("thread_resume", thread_id=thread_id)
        return self.threads.get(thread_id, ThreadInfo(id=thread_id))

    async def thread_list(
        self, limit: int = 50, cursor: str | None = None, use_state_db_only: bool | None = None
    ) -> list[ThreadInfo]:
        self._record("thread_list", limit=limit)
        return self.list_result

    async def thread_read(self, thread_id: str) -> dict[str, object]:
        self._record("thread_read", thread_id=thread_id)
        return self.read_result

    async def thread_archive(self, thread_id: str) -> None:
        self._record("thread_archive", thread_id=thread_id)

    async def turn_start(
        self,
        thread_id: str,
        text: str,
        cwd: str,
        approval_policy: ApprovalPolicy = "never",
        sandbox_policy: SandboxPolicy | None = None,
        effort: Effort | None = None,
    ) -> dict[str, object]:
        self._record("turn_start", thread_id=thread_id, text=text, cwd=cwd)
        return {}

    async def turn_steer(
        self, thread_id: str, expected_turn_id: str, text: str
    ) -> dict[str, object]:
        self._record(
            "turn_steer", thread_id=thread_id, expected_turn_id=expected_turn_id, text=text
        )
        return {}

    async def turn_interrupt(self, thread_id: str, turn_id: str | None = None) -> None:
        self._record("turn_interrupt", thread_id=thread_id, turn_id=turn_id)

    async def inject_items(self, thread_id: str, items: list[dict[str, object]]) -> None:
        self._record("inject_items", thread_id=thread_id, items=items)

    async def respond_approval(self, request_id: int, decision: Decision) -> None:
        self._record("respond_approval", request_id=request_id, decision=decision)

    def events(self, lane: str | None = None) -> AsyncIterator[LaneEvent]:
        return _aiter(self.event_log)

    def raw_events(self, lane: str | None = None) -> AsyncIterator[dict[str, object]]:
        return _aiter(self.raw_log)


def make_ctx(store: Registry, client: FakeLaneClient | None = None) -> Ctx:
    """Build a handler Ctx with a fake client + the given store (ADR-0006)."""
    return Ctx(
        client=client if client is not None else FakeLaneClient(),
        registry=store,
        log=structlog.get_logger(),
        abort=asyncio.Event(),
    )


class FakeClock:
    """A controllable clock for deterministic time-trigger tests."""

    def __init__(self, start: datetime) -> None:
        self._t = start

    def __call__(self) -> datetime:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class FakeSupervisedClient(FakeLaneClient):
    """A FakeLaneClient with the supervised lifecycle (wait_closed/close)."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = asyncio.Event()
        self.resumed: list[str] = []

    async def thread_resume(self, thread_id: str) -> ThreadInfo:
        self.resumed.append(thread_id)
        return await super().thread_resume(thread_id)

    async def wait_closed(self) -> None:
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()
