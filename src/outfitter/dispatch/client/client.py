"""The App Server client facade.

Transport + router + typed primitives. The ONLY place that speaks the App Server
protocol (``.claude/rules/client.md``). Importable standalone (no daemon). No
business logic here — orchestration lives in ``core/``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self

from pydantic import BaseModel

from .errors import ProtocolError, TransportError
from .events import LaneEvent
from .models import (
    ApprovalPolicy,
    ApprovalsReviewer,
    ClientInfo,
    Decision,
    Effort,
    InitializeParams,
    InitializeResult,
    InjectItemsParams,
    Personality,
    ReasoningSummary,
    SandboxPolicy,
    TextInput,
    ThreadArchiveParams,
    ThreadCompactStartParams,
    ThreadForkParams,
    ThreadGoal,
    ThreadGoalClearParams,
    ThreadGoalGetParams,
    ThreadGoalGetResult,
    ThreadGoalResult,
    ThreadGoalSetParams,
    ThreadGoalStatus,
    ThreadInfo,
    ThreadListParams,
    ThreadListResult,
    ThreadReadParams,
    ThreadResult,
    ThreadResumeParams,
    ThreadRollbackParams,
    ThreadSandbox,
    ThreadSetNameParams,
    ThreadStartParams,
    TurnInterruptParams,
    TurnStartParams,
    TurnSteerParams,
)
from .router import Router
from .transport import Transport

_DEFAULT_CLIENT_INFO = ClientInfo(name="dispatch", version="0")


def _dump(model: BaseModel) -> dict[str, object]:
    # All params are WireModel/BaseModel; serialize with aliases, drop None.
    return model.model_dump(by_alias=True, exclude_none=True)


class AppServerClient:
    """Typed async client over one app-server connection."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._router = Router()
        self._id = 0
        self._read_task: asyncio.Task[None] | None = None
        self._closed = False
        self._closed_event = asyncio.Event()  # set when the read loop ends (EOF or close)

    async def start(self) -> None:
        """Begin consuming the transport. Idempotent."""
        if self._read_task is None:
            self._read_task = asyncio.create_task(self._read_loop())

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _read_loop(self) -> None:
        try:
            try:
                while True:
                    message = await self._transport.receive()
                    if message is None:
                        break
                    self._router.handle(message)
            except (TransportError, ProtocolError) as exc:
                self._router.fail_all(exc)
                return
            self._router.fail_all(TransportError("app-server stream closed (stdout EOF)"))
        finally:
            self._closed_event.set()  # wake supervisors waiting on wait_closed()

    async def wait_closed(self) -> None:
        """Block until the read loop ends — EOF (app-server crash) or ``close()``."""
        await self._closed_event.wait()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _request(
        self, method: str, params: dict[str, object] | None = None
    ) -> dict[str, object]:
        request_id = self._next_id()
        fut = self._router.new_request(request_id)
        message: dict[str, object] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        await self._transport.send(message)
        try:
            return await fut
        except asyncio.CancelledError:
            # A bounded caller (e.g. attach's wait_for) timed out: drop the pending
            # request so abandoned ids don't accumulate in the router.
            self._router.discard_request(request_id)
            raise

    async def _notify(self, method: str, params: dict[str, object] | None = None) -> None:
        message: dict[str, object] = {"method": method}
        if params is not None:
            message["params"] = params
        await self._transport.send(message)

    # --- lifecycle ------------------------------------------------------------

    async def initialize(
        self,
        client_info: ClientInfo = _DEFAULT_CLIENT_INFO,
        capabilities: dict[str, bool] | None = None,
    ) -> InitializeResult:
        params = InitializeParams(
            client_info=client_info,
            capabilities=capabilities if capabilities is not None else {"experimentalApi": False},
        )
        result = await self._request("initialize", _dump(params))
        await self._notify("initialized", {})
        return InitializeResult.model_validate(result)

    # --- threads --------------------------------------------------------------

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
        params = ThreadStartParams(
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
        result = await self._request("thread/start", _dump(params))
        return ThreadResult.model_validate(result).thread

    async def thread_resume(self, thread_id: str) -> ThreadInfo:
        result = await self._request(
            "thread/resume", _dump(ThreadResumeParams(thread_id=thread_id))
        )
        return ThreadResult.model_validate(result).thread

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
        params = ThreadForkParams(
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
        result = await self._request("thread/fork", _dump(params))
        return ThreadResult.model_validate(result).thread

    async def thread_list(
        self, limit: int = 50, cursor: str | None = None, use_state_db_only: bool | None = None
    ) -> list[ThreadInfo]:
        params = ThreadListParams(limit=limit, cursor=cursor, use_state_db_only=use_state_db_only)
        result = await self._request("thread/list", _dump(params))
        return ThreadListResult.model_validate(result).data

    async def thread_read(self, thread_id: str, include_turns: bool = False) -> dict[str, object]:
        return await self._request(
            "thread/read", _dump(ThreadReadParams(thread_id=thread_id, include_turns=include_turns))
        )

    async def thread_archive(self, thread_id: str) -> None:
        await self._request("thread/archive", _dump(ThreadArchiveParams(thread_id=thread_id)))

    async def thread_set_name(self, thread_id: str, name: str) -> None:
        await self._request(
            "thread/name/set", _dump(ThreadSetNameParams(thread_id=thread_id, name=name))
        )

    async def thread_rollback(self, thread_id: str, num_turns: int) -> ThreadInfo:
        result = await self._request(
            "thread/rollback", _dump(ThreadRollbackParams(thread_id=thread_id, num_turns=num_turns))
        )
        return ThreadResult.model_validate(result).thread

    async def thread_compact_start(self, thread_id: str) -> None:
        await self._request(
            "thread/compact/start", _dump(ThreadCompactStartParams(thread_id=thread_id))
        )

    async def thread_goal_get(self, thread_id: str) -> ThreadGoal | None:
        result = await self._request(
            "thread/goal/get", _dump(ThreadGoalGetParams(thread_id=thread_id))
        )
        return ThreadGoalGetResult.model_validate(result).goal

    async def thread_goal_set(
        self,
        thread_id: str,
        *,
        objective: str | None = None,
        status: ThreadGoalStatus | None = None,
        token_budget: int | None = None,
    ) -> ThreadGoal:
        params = ThreadGoalSetParams(
            thread_id=thread_id,
            objective=objective,
            status=status,
            token_budget=token_budget,
        )
        result = await self._request("thread/goal/set", _dump(params))
        return ThreadGoalResult.model_validate(result).goal

    async def thread_goal_clear(self, thread_id: str) -> None:
        await self._request("thread/goal/clear", _dump(ThreadGoalClearParams(thread_id=thread_id)))

    # --- turns + injection ----------------------------------------------------

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
        params = TurnStartParams(
            thread_id=thread_id,
            input=[TextInput(text=text)],
            cwd=cwd,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            sandbox_policy=sandbox_policy if sandbox_policy is not None else SandboxPolicy(),
            effort=effort,
            summary=summary,
            model=model,
            output_schema=output_schema,
            personality=personality,
        )
        return await self._request("turn/start", _dump(params))

    async def turn_steer(
        self, thread_id: str, expected_turn_id: str, text: str
    ) -> dict[str, object]:
        params = TurnSteerParams(
            thread_id=thread_id, expected_turn_id=expected_turn_id, input=[TextInput(text=text)]
        )
        return await self._request("turn/steer", _dump(params))

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> None:
        await self._request(
            "turn/interrupt", _dump(TurnInterruptParams(thread_id=thread_id, turn_id=turn_id))
        )

    async def inject_items(self, thread_id: str, items: list[dict[str, object]]) -> None:
        await self._request(
            "thread/inject_items", _dump(InjectItemsParams(thread_id=thread_id, items=items))
        )

    # --- approvals ------------------------------------------------------------

    async def respond_approval(self, request_id: int, decision: Decision) -> None:
        """Reply to a server->client approval request on the same stream."""
        await self._transport.send({"id": request_id, "result": {"decision": decision}})

    # --- event streams --------------------------------------------------------

    def events(self, lane: str | None = None) -> AsyncIterator[LaneEvent]:
        """Normalized LaneEvents for one lane, or all lanes when ``lane`` is None."""
        return self._router.events.subscribe(lane)

    def raw_events(self, lane: str | None = None) -> AsyncIterator[dict[str, object]]:
        """Raw notifications/server-requests for one lane (content for ``show``)."""
        return self._router.raw.subscribe(lane)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Fail any in-flight requests and close the event/raw streams up front, so
        # callers never deadlock if close() wins the race against stdout EOF.
        self._router.fail_all(TransportError("client closed"))
        if self._read_task is not None:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._read_task
        await self._transport.close()
        self._closed_event.set()
