"""The App Server client facade.

Transport + router + typed primitives. The ONLY place that speaks the App Server
protocol (``.claude/rules/client.md``). Importable standalone (no daemon). No
business logic here — orchestration lives in ``core/``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from types import TracebackType
from typing import Self

from pydantic import BaseModel, ValidationError

from .errors import ClientError, ProtocolError, TransportError
from .events import AccountRateLimitsUpdated, LaneEvent, ServerRequestReceived
from .models import (
    AccountRateLimitsResult,
    AccountReadResult,
    AccountUsageResult,
    AppModel,
    ApprovalPolicy,
    ApprovalsReviewer,
    ClientInfo,
    ConfigInfo,
    Decision,
    Effort,
    InitializeParams,
    InitializeResult,
    InjectItemsParams,
    JsonRpcError,
    JsonRpcId,
    ModelListParams,
    ModelListResult,
    PermissionProfileListParams,
    PermissionProfileListResult,
    PermissionProfileSummary,
    Personality,
    ReasoningSummary,
    SandboxPolicy,
    SortDirection,
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
    ThreadItemsListParams,
    ThreadItemsPage,
    ThreadListCwdFilter,
    ThreadListParams,
    ThreadListResult,
    ThreadReadParams,
    ThreadResult,
    ThreadResumeInitialTurnsPageParams,
    ThreadResumeParams,
    ThreadResumeResult,
    ThreadRollbackParams,
    ThreadSandbox,
    ThreadSearchParams,
    ThreadSearchResult,
    ThreadSetNameParams,
    ThreadSortKey,
    ThreadSourceKind,
    ThreadStartParams,
    ThreadTurnsListParams,
    ThreadTurnsPage,
    ThreadUnarchiveParams,
    TurnInterruptParams,
    TurnItemsView,
    TurnStartParams,
    TurnSteerParams,
    UserInput,
)
from .router import Router
from .transport import Transport

_DEFAULT_CLIENT_INFO = ClientInfo(name="dispatch", version="0")


def _dump(model: BaseModel) -> dict[str, object]:
    # All params are WireModel/BaseModel; serialize with aliases, drop None.
    return model.model_dump(by_alias=True, exclude_none=True)


def _user_inputs(text: str, input_items: list[UserInput] | None) -> list[UserInput]:
    inputs: list[UserInput] = []
    if text:
        inputs.append(TextInput(text=text))
    if input_items:
        inputs.extend(input_items)
    if not inputs:
        raise ProtocolError("turn input requires text or at least one structured item")
    return inputs


def _parse_page(
    method: str,
    result: dict[str, object],
    page_type: type[ThreadTurnsPage] | type[ThreadItemsPage],
    requested_cursor: str | None,
) -> ThreadTurnsPage | ThreadItemsPage:
    try:
        page = page_type.model_validate(result)
    except ValidationError as exc:
        raise ProtocolError(f"malformed {method} response: {exc}") from exc
    if requested_cursor is not None and page.next_cursor == requested_cursor:
        raise ProtocolError(f"{method} repeated pagination cursor {requested_cursor!r}")
    return page


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
            except ClientError as exc:
                self._router.fail_all(exc)
                return
            except Exception as exc:
                self._router.fail_all(TransportError(f"app-server read loop failed: {exc}"))
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
            capabilities=(
                capabilities
                if capabilities is not None
                else {
                    "experimentalApi": True,
                    "mcpServerOpenaiFormElicitation": True,
                }
            ),
        )
        result = await self._request("initialize", _dump(params))
        await self._notify("initialized", {})
        return InitializeResult.model_validate(result)

    # --- config/models ---------------------------------------------------------

    async def config_read(self) -> ConfigInfo:
        result = await self._request("config/read", {})
        payload = result.get("config") if isinstance(result.get("config"), dict) else result
        return ConfigInfo.model_validate(payload)

    async def account_read(self) -> AccountReadResult:
        result = await self._request("account/read", {"refreshToken": False})
        return AccountReadResult.model_validate(result)

    async def account_rate_limits_read(self) -> AccountRateLimitsResult:
        result = await self._request("account/rateLimits/read", {})
        return AccountRateLimitsResult.model_validate(result)

    async def account_usage_read(self) -> AccountUsageResult:
        result = await self._request("account/usage/read", {})
        return AccountUsageResult.model_validate(result)

    async def model_list(self) -> list[AppModel]:
        models: list[AppModel] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params = ModelListParams(cursor=cursor, include_hidden=True)
            result = await self._request("model/list", _dump(params))
            page = ModelListResult.model_validate(result)
            models.extend(page.data)
            cursor = page.next_cursor
            if cursor is None:
                return models
            if cursor in seen_cursors:
                raise ProtocolError(f"model/list repeated pagination cursor {cursor!r}")
            seen_cursors.add(cursor)

    async def permission_profile_list(
        self, *, cwd: str | None = None, limit: int | None = None
    ) -> list[PermissionProfileSummary]:
        profiles: list[PermissionProfileSummary] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params = PermissionProfileListParams(cursor=cursor, cwd=cwd, limit=limit)
            result = await self._request("permissionProfile/list", _dump(params))
            page = PermissionProfileListResult.model_validate(result)
            profiles.extend(page.data)
            cursor = page.next_cursor
            if cursor is None:
                return profiles
            if cursor in seen_cursors:
                raise ProtocolError(f"permissionProfile/list repeated pagination cursor {cursor!r}")
            seen_cursors.add(cursor)

    # --- threads --------------------------------------------------------------

    async def thread_start(
        self,
        cwd: str | None,
        permission_profile: str | None = None,
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
    ) -> ThreadInfo:
        params = ThreadStartParams(
            cwd=cwd,
            permissions=permission_profile,
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

    async def thread_resume(
        self,
        thread_id: str,
        *,
        permission_profile: str | None = None,
        exclude_turns: bool | None = None,
        initial_turns_page: ThreadResumeInitialTurnsPageParams | None = None,
    ) -> ThreadInfo:
        response = await self.thread_resume_full(
            thread_id,
            permission_profile=permission_profile,
            exclude_turns=exclude_turns,
            initial_turns_page=initial_turns_page,
        )
        return response.thread

    async def thread_resume_full(
        self,
        thread_id: str,
        *,
        permission_profile: str | None = None,
        exclude_turns: bool | None = None,
        initial_turns_page: ThreadResumeInitialTurnsPageParams | None = None,
    ) -> ThreadResumeResult:
        result = await self._request(
            "thread/resume",
            _dump(
                ThreadResumeParams(
                    thread_id=thread_id,
                    permissions=permission_profile,
                    exclude_turns=exclude_turns,
                    initial_turns_page=initial_turns_page,
                )
            ),
        )
        try:
            return ThreadResumeResult.model_validate(result)
        except ValidationError as exc:
            raise ProtocolError(f"malformed thread/resume response: {exc}") from exc

    async def thread_turns_list(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: SortDirection | None = None,
        items_view: TurnItemsView | None = None,
    ) -> ThreadTurnsPage:
        params = ThreadTurnsListParams(
            thread_id=thread_id,
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            items_view=items_view,
        )
        result = await self._request("thread/turns/list", _dump(params))
        page = _parse_page("thread/turns/list", result, ThreadTurnsPage, cursor)
        assert isinstance(page, ThreadTurnsPage)
        return page

    async def thread_items_list(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: SortDirection | None = None,
        turn_id: str | None = None,
    ) -> ThreadItemsPage:
        params = ThreadItemsListParams(
            thread_id=thread_id,
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            turn_id=turn_id,
        )
        result = await self._request("thread/items/list", _dump(params))
        page = _parse_page("thread/items/list", result, ThreadItemsPage, cursor)
        assert isinstance(page, ThreadItemsPage)
        return page

    async def thread_fork(
        self,
        thread_id: str,
        *,
        cwd: str | None = None,
        permission_profile: str | None = None,
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
    ) -> ThreadInfo:
        params = ThreadForkParams(
            thread_id=thread_id,
            cwd=cwd,
            permissions=permission_profile,
            sandbox=sandbox,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            base_instructions=base_instructions,
            developer_instructions=developer_instructions,
            service_tier=service_tier,
            model=model,
            model_provider=model_provider,
            last_turn_id=last_turn_id,
            ephemeral=ephemeral,
        )
        result = await self._request("thread/fork", _dump(params))
        return ThreadResult.model_validate(result).thread

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
        parent_thread_id: str | None = None,
        ancestor_thread_id: str | None = None,
    ) -> list[ThreadInfo]:
        params = ThreadListParams(
            limit=limit,
            cursor=cursor,
            archived=archived,
            cwd=cwd,
            model_providers=model_providers,
            search_term=search_term,
            sort_direction=sort_direction,
            sort_key=sort_key,
            source_kinds=source_kinds,
            parent_thread_id=parent_thread_id,
            ancestor_thread_id=ancestor_thread_id,
            use_state_db_only=use_state_db_only,
        )
        result = await self._request("thread/list", _dump(params))
        return ThreadListResult.model_validate(result).data

    async def thread_read(self, thread_id: str, include_turns: bool = False) -> dict[str, object]:
        return await self._request(
            "thread/read", _dump(ThreadReadParams(thread_id=thread_id, include_turns=include_turns))
        )

    async def thread_archive(self, thread_id: str) -> None:
        await self._request("thread/archive", _dump(ThreadArchiveParams(thread_id=thread_id)))

    async def thread_unarchive(self, thread_id: str) -> ThreadInfo:
        result = await self._request(
            "thread/unarchive", _dump(ThreadUnarchiveParams(thread_id=thread_id))
        )
        return ThreadResult.model_validate(result).thread

    async def thread_set_name(self, thread_id: str, name: str) -> None:
        await self._request(
            "thread/name/set", _dump(ThreadSetNameParams(thread_id=thread_id, name=name))
        )

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
        params = ThreadSearchParams(
            search_term=search_term,
            archived=archived,
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            sort_key=sort_key,
            source_kinds=source_kinds,
        )
        result = await self._request("thread/search", _dump(params))
        return ThreadSearchResult.model_validate(result)

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
        input_items: list[UserInput] | None = None,
        permission_profile: str | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        effort: Effort | None = None,
        summary: ReasoningSummary | None = None,
        model: str | None = None,
        service_tier: str | None = None,
        output_schema: dict[str, object] | None = None,
        personality: Personality | None = None,
    ) -> dict[str, object]:
        params = TurnStartParams(
            thread_id=thread_id,
            input=_user_inputs(text, input_items),
            cwd=cwd,
            permissions=permission_profile,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            sandbox_policy=sandbox_policy,
            effort=effort,
            summary=summary,
            model=model,
            service_tier=service_tier,
            output_schema=output_schema,
            personality=personality,
        )
        return await self._request("turn/start", _dump(params))

    async def turn_steer(
        self,
        thread_id: str,
        expected_turn_id: str,
        text: str,
        input_items: list[UserInput] | None = None,
    ) -> dict[str, object]:
        params = TurnSteerParams(
            thread_id=thread_id,
            expected_turn_id=expected_turn_id,
            input=_user_inputs(text, input_items),
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

    async def respond_server_request(
        self,
        request_id: JsonRpcId,
        *,
        result: Mapping[str, object] | None = None,
        error: JsonRpcError | None = None,
    ) -> None:
        """Reply to a server request with exactly one JSON-RPC result or error."""
        if (result is None) == (error is None):
            raise ProtocolError("server request response requires exactly one of result or error")
        message: dict[str, object] = {"id": request_id}
        if result is not None:
            message["result"] = dict(result)
        elif error is not None:
            message["error"] = _dump(error)
        await self._transport.send(message)

    async def respond_approval(self, request_id: JsonRpcId, decision: Decision) -> None:
        """Reply to a server->client approval request on the same stream."""
        await self.respond_server_request(request_id, result={"decision": decision})

    # --- event streams --------------------------------------------------------

    def events(self, lane: str | None = None) -> AsyncIterator[LaneEvent]:
        """Normalized LaneEvents for one lane, or all lanes when ``lane`` is None."""
        return self._router.events.subscribe(lane)

    def raw_events(self, lane: str | None = None) -> AsyncIterator[dict[str, object]]:
        """Raw notifications/server-requests for one lane (content for ``show``)."""
        return self._router.raw.subscribe(lane)

    def server_requests(self, lane: str | None = None) -> AsyncIterator[ServerRequestReceived]:
        """Server requests for one lane, or all requests including threadless ones."""
        return self._router.requests.subscribe(lane)

    def account_events(self) -> AsyncIterator[AccountRateLimitsUpdated]:
        """Normalized provider-level account notifications."""
        return self._router.account_events.subscribe(None)

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
