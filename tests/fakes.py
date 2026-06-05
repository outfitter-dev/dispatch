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
    ApprovalsReviewer,
    Decision,
    Effort,
    Personality,
    ReasoningSummary,
    SandboxPolicy,
    SortDirection,
    ThreadGoal,
    ThreadGoalStatus,
    ThreadInfo,
    ThreadSandbox,
    ThreadSearchMatch,
    ThreadSearchResult,
    ThreadSortKey,
    ThreadSourceKind,
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
        self.search_result = ThreadSearchResult()
        self.goal_result: ThreadGoal | None = None
        self.event_log: list[LaneEvent] = []
        self.raw_log: list[dict[str, object]] = []

    def _record(self, name: str, **kwargs: object) -> None:
        self.calls.append((name, kwargs))

    async def thread_start(
        self,
        cwd: str | None,
        sandbox: ThreadSandbox = "read-only",
        approval_policy: ApprovalPolicy = "never",
        approvals_reviewer: ApprovalsReviewer | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        personality: Personality | None = None,
        service_tier: str | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        ephemeral: bool = False,
    ) -> ThreadInfo:
        self._record(
            "thread_start",
            cwd=cwd,
            sandbox=sandbox,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            base_instructions=base_instructions,
            developer_instructions=developer_instructions,
            personality=personality,
            service_tier=service_tier,
            model=model,
            model_provider=model_provider,
            ephemeral=ephemeral,
        )
        thread = ThreadInfo(id=self.next_thread_id, ephemeral=ephemeral, cwd=cwd)
        self.threads[thread.id] = thread
        return thread

    async def thread_resume(self, thread_id: str) -> ThreadInfo:
        self._record("thread_resume", thread_id=thread_id)
        return self.threads.get(thread_id, ThreadInfo(id=thread_id))

    async def thread_fork(
        self,
        thread_id: str,
        *,
        cwd: str | None = None,
        sandbox: ThreadSandbox | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        service_tier: str | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        ephemeral: bool = False,
    ) -> ThreadInfo:
        self._record(
            "thread_fork",
            thread_id=thread_id,
            cwd=cwd,
            sandbox=sandbox,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            base_instructions=base_instructions,
            developer_instructions=developer_instructions,
            service_tier=service_tier,
            model=model,
            model_provider=model_provider,
            ephemeral=ephemeral,
        )
        fork = ThreadInfo(id=f"{thread_id}-fork", ephemeral=ephemeral, cwd=cwd)
        self.threads[fork.id] = fork
        return fork

    async def thread_list(
        self, limit: int = 50, cursor: str | None = None, use_state_db_only: bool | None = None
    ) -> list[ThreadInfo]:
        self._record("thread_list", limit=limit, use_state_db_only=use_state_db_only)
        return self.list_result

    async def thread_read(self, thread_id: str, include_turns: bool = False) -> dict[str, object]:
        self._record("thread_read", thread_id=thread_id, include_turns=include_turns)
        if not self.read_result:
            thread = self.threads.get(thread_id, ThreadInfo(id=thread_id))
            return {"thread": thread.model_dump(by_alias=True, exclude_none=True)}
        return self.read_result

    async def thread_archive(self, thread_id: str) -> None:
        self._record("thread_archive", thread_id=thread_id)

    async def thread_unarchive(self, thread_id: str) -> ThreadInfo:
        self._record("thread_unarchive", thread_id=thread_id)
        return self.threads.get(thread_id, ThreadInfo(id=thread_id))

    async def thread_set_name(self, thread_id: str, name: str) -> None:
        self._record("thread_set_name", thread_id=thread_id, display_name=name)

    async def thread_search(
        self,
        search_term: str,
        *,
        archived: bool | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: SortDirection | None = None,
        sort_key: ThreadSortKey | None = None,
        source_kinds: list[ThreadSourceKind] | None = None,
    ) -> ThreadSearchResult:
        self._record(
            "thread_search",
            search_term=search_term,
            archived=archived,
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            sort_key=sort_key,
            source_kinds=source_kinds,
        )
        if limit is None:
            return self.search_result
        return ThreadSearchResult(
            data=[
                ThreadSearchMatch(snippet=match.snippet, thread=match.thread)
                for match in self.search_result.data[:limit]
            ],
            next_cursor=self.search_result.next_cursor,
            backwards_cursor=self.search_result.backwards_cursor,
        )

    async def thread_rollback(self, thread_id: str, num_turns: int) -> ThreadInfo:
        self._record("thread_rollback", thread_id=thread_id, num_turns=num_turns)
        return self.threads.get(thread_id, ThreadInfo(id=thread_id))

    async def thread_compact_start(self, thread_id: str) -> None:
        self._record("thread_compact_start", thread_id=thread_id)

    async def thread_goal_get(self, thread_id: str) -> ThreadGoal | None:
        self._record("thread_goal_get", thread_id=thread_id)
        return self.goal_result

    async def thread_goal_set(
        self,
        thread_id: str,
        *,
        objective: str | None = None,
        status: ThreadGoalStatus | None = None,
        token_budget: int | None = None,
    ) -> ThreadGoal:
        self._record(
            "thread_goal_set",
            thread_id=thread_id,
            objective=objective,
            status=status,
            token_budget=token_budget,
        )
        goal = ThreadGoal(
            thread_id=thread_id,
            objective=objective or (self.goal_result.objective if self.goal_result else ""),
            status=status or (self.goal_result.status if self.goal_result else "active"),
            tokens_used=0,
            time_used_seconds=0,
            created_at=1,
            updated_at=2,
            token_budget=token_budget,
        )
        self.goal_result = goal
        return goal

    async def thread_goal_clear(self, thread_id: str) -> None:
        self._record("thread_goal_clear", thread_id=thread_id)
        self.goal_result = None

    async def turn_start(
        self,
        thread_id: str,
        text: str,
        cwd: str,
        approval_policy: ApprovalPolicy = "never",
        approvals_reviewer: ApprovalsReviewer | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        effort: Effort | None = None,
        summary: ReasoningSummary | None = None,
        model: str | None = None,
        output_schema: dict[str, object] | None = None,
        personality: Personality | None = None,
    ) -> dict[str, object]:
        self._record(
            "turn_start",
            thread_id=thread_id,
            text=text,
            cwd=cwd,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            sandbox_policy=sandbox_policy.model_dump(mode="python") if sandbox_policy else None,
            effort=effort,
            summary=summary,
            model=model,
            output_schema=output_schema,
            personality=personality,
        )
        return {}

    async def turn_steer(
        self, thread_id: str, expected_turn_id: str, text: str
    ) -> dict[str, object]:
        self._record(
            "turn_steer", thread_id=thread_id, expected_turn_id=expected_turn_id, text=text
        )
        return {}

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> None:
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
