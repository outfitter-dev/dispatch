"""Handler context and dependency injection (ADR-0006).

Every handler is ``async def handler(input, ctx) -> Output``. ``Ctx`` carries
exactly the four dependencies handlers may touch — no surface-specific concerns
(argv, MCP session, socket) ever leak onto it. Handlers never import
infrastructure directly; tests construct a ``Ctx`` with a fake client + temp-dir
store.

``LaneClient`` is the handler↔client contract — the subset of the App Server
client handlers use. The real ``AppServerClient`` satisfies it structurally, and
mypy enforces that conformance, so the protocol can't silently drift.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import structlog

from outfitter.dispatch.client.events import LaneEvent
from outfitter.dispatch.client.models import (
    AppModel,
    ApprovalPolicy,
    ApprovalsReviewer,
    ConfigInfo,
    Decision,
    Effort,
    Personality,
    ReasoningSummary,
    SandboxPolicy,
    SortDirection,
    ThreadGoal,
    ThreadGoalStatus,
    ThreadInfo,
    ThreadListCwdFilter,
    ThreadSandbox,
    ThreadSearchResult,
    ThreadSortKey,
    ThreadSourceKind,
)
from outfitter.dispatch.config import CapturePolicy, RuntimePolicy

if TYPE_CHECKING:
    from outfitter.dispatch.registry.store import Registry


class LaneClient(Protocol):
    """The App Server primitives handlers depend on (ADR-0006 DI seam)."""

    async def config_read(self) -> ConfigInfo: ...

    async def model_list(self) -> list[AppModel]: ...

    async def thread_start(
        self,
        cwd: str | None,
        sandbox: ThreadSandbox | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        personality: Personality | None = None,
        service_tier: str | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        ephemeral: bool = False,
    ) -> ThreadInfo: ...

    async def thread_resume(
        self,
        thread_id: str,
        *,
        exclude_turns: bool | None = None,
    ) -> ThreadInfo: ...

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
        last_turn_id: str | None = None,
        ephemeral: bool = False,
    ) -> ThreadInfo: ...

    async def thread_list(
        self,
        limit: int = 50,
        cursor: str | None = None,
        use_state_db_only: bool | None = None,
        *,
        archived: bool | None = None,
        cwd: ThreadListCwdFilter | None = None,
        model_providers: list[str] | None = None,
        search_term: str | None = None,
        sort_direction: SortDirection | None = None,
        sort_key: ThreadSortKey | None = None,
        source_kinds: list[ThreadSourceKind] | None = None,
    ) -> list[ThreadInfo]: ...

    async def thread_read(
        self, thread_id: str, include_turns: bool = False
    ) -> dict[str, object]: ...

    async def thread_archive(self, thread_id: str) -> None: ...

    async def thread_unarchive(self, thread_id: str) -> ThreadInfo: ...

    async def thread_set_name(self, thread_id: str, name: str) -> None: ...

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
    ) -> ThreadSearchResult: ...

    async def thread_rollback(self, thread_id: str, num_turns: int) -> ThreadInfo: ...

    async def thread_compact_start(self, thread_id: str) -> None: ...

    async def thread_goal_get(self, thread_id: str) -> ThreadGoal | None: ...

    async def thread_goal_set(
        self,
        thread_id: str,
        *,
        objective: str | None = None,
        status: ThreadGoalStatus | None = None,
        token_budget: int | None = None,
    ) -> ThreadGoal: ...

    async def thread_goal_clear(self, thread_id: str) -> None: ...

    async def turn_start(
        self,
        thread_id: str,
        text: str,
        cwd: str,
        approval_policy: ApprovalPolicy | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        effort: Effort | None = None,
        summary: ReasoningSummary | None = None,
        model: str | None = None,
        service_tier: str | None = None,
        output_schema: dict[str, object] | None = None,
        personality: Personality | None = None,
    ) -> dict[str, object]: ...

    async def turn_steer(
        self, thread_id: str, expected_turn_id: str, text: str
    ) -> dict[str, object]: ...

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> None: ...

    async def inject_items(self, thread_id: str, items: list[dict[str, object]]) -> None: ...

    async def respond_approval(self, request_id: int, decision: Decision) -> None: ...

    def events(self, lane: str | None = None) -> AsyncIterator[LaneEvent]: ...

    def raw_events(self, lane: str | None = None) -> AsyncIterator[dict[str, object]]: ...


@dataclass
class Ctx:
    """Injected into every handler. Small and stable by design."""

    client: LaneClient
    registry: Registry
    log: structlog.stdlib.BoundLogger
    abort: asyncio.Event
    policy: RuntimePolicy = field(default_factory=RuntimePolicy)
    capture: CapturePolicy = field(default_factory=CapturePolicy)
