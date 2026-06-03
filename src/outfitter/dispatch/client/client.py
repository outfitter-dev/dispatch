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
    ClientInfo,
    Decision,
    Effort,
    InitializeParams,
    InitializeResult,
    InjectItemsParams,
    SandboxPolicy,
    TextInput,
    ThreadArchiveParams,
    ThreadInfo,
    ThreadListParams,
    ThreadListResult,
    ThreadReadParams,
    ThreadResult,
    ThreadResumeParams,
    ThreadSandbox,
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
            while True:
                message = await self._transport.receive()
                if message is None:
                    break
                self._router.handle(message)
        except (TransportError, ProtocolError) as exc:
            self._router.fail_all(exc)
            return
        self._router.fail_all(TransportError("app-server stream closed (stdout EOF)"))

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
        return await fut

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
        cwd: str,
        sandbox: ThreadSandbox = "read-only",
        approval_policy: ApprovalPolicy = "never",
        ephemeral: bool = False,
    ) -> ThreadInfo:
        params = ThreadStartParams(
            cwd=cwd, sandbox=sandbox, approval_policy=approval_policy, ephemeral=ephemeral
        )
        result = await self._request("thread/start", _dump(params))
        return ThreadResult.model_validate(result).thread

    async def thread_resume(self, thread_id: str) -> ThreadInfo:
        result = await self._request(
            "thread/resume", _dump(ThreadResumeParams(thread_id=thread_id))
        )
        return ThreadResult.model_validate(result).thread

    async def thread_list(
        self, limit: int = 50, cursor: str | None = None, use_state_db_only: bool | None = None
    ) -> list[ThreadInfo]:
        params = ThreadListParams(limit=limit, cursor=cursor, use_state_db_only=use_state_db_only)
        result = await self._request("thread/list", _dump(params))
        return ThreadListResult.model_validate(result).data

    async def thread_read(self, thread_id: str) -> dict[str, object]:
        return await self._request("thread/read", _dump(ThreadReadParams(thread_id=thread_id)))

    async def thread_archive(self, thread_id: str) -> None:
        await self._request("thread/archive", _dump(ThreadArchiveParams(thread_id=thread_id)))

    # --- turns + injection ----------------------------------------------------

    async def turn_start(
        self,
        thread_id: str,
        text: str,
        cwd: str,
        approval_policy: ApprovalPolicy = "never",
        sandbox_policy: SandboxPolicy | None = None,
        effort: Effort | None = None,
    ) -> dict[str, object]:
        params = TurnStartParams(
            thread_id=thread_id,
            input=[TextInput(text=text)],
            cwd=cwd,
            approval_policy=approval_policy,
            sandbox_policy=sandbox_policy if sandbox_policy is not None else SandboxPolicy(),
            effort=effort,
        )
        return await self._request("turn/start", _dump(params))

    async def turn_steer(
        self, thread_id: str, expected_turn_id: str, text: str
    ) -> dict[str, object]:
        params = TurnSteerParams(
            thread_id=thread_id, expected_turn_id=expected_turn_id, input=[TextInput(text=text)]
        )
        return await self._request("turn/steer", _dump(params))

    async def turn_interrupt(self, thread_id: str, turn_id: str | None = None) -> None:
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
