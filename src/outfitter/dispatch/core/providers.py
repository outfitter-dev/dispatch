"""Typed provider routing for binding-scoped lane operations.

The first implementation registers only the owned default Codex binding.  The
router fixes a lane's provider, binding, native session, support, availability,
and durability facts before provider I/O.  Future provider adapters can expose
their own operation-shaped methods without implementing ``LaneClient``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from outfitter.dispatch.client.errors import AppServerError, ClientError, ProtocolError
from outfitter.dispatch.client.events import (
    AccountRateLimitsUpdated,
    LaneEvent,
    ServerRequestReceived,
)
from outfitter.dispatch.client.models import (
    AccountRateLimitsResult,
    AccountReadResult,
    AccountUsageResult,
    AppModel,
    ApprovalPolicy,
    ApprovalsReviewer,
    ConfigInfo,
    Decision,
    Effort,
    JsonRpcError,
    JsonRpcId,
    PermissionProfileSummary,
    Personality,
    ReasoningSummary,
    SandboxPolicy,
    SortDirection,
    ThreadGoal,
    ThreadGoalStatus,
    ThreadInfo,
    ThreadItemsPage,
    ThreadListCwdFilter,
    ThreadResumeInitialTurnsPageParams,
    ThreadResumeResult,
    ThreadSandbox,
    ThreadSearchResult,
    ThreadSortKey,
    ThreadSourceKind,
    ThreadTurnsPage,
    TurnItemsView,
    UserInput,
)
from outfitter.dispatch.client.native_queue import QueuedSubmission, ThreadQueuePage
from outfitter.dispatch.contracts.context import LaneClient
from outfitter.dispatch.contracts.errors import CapabilityUnavailableError
from outfitter.dispatch.registry.delivery import DeliveryTransport
from outfitter.dispatch.registry.models import Lane
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID

from .turn_settings import TurnStartSettings

if TYPE_CHECKING:
    from outfitter.dispatch.contracts.context import Ctx


class ProviderAction(StrEnum):
    CONFIG_READ = "config_read"
    MODEL_READ = "model_read"
    ACCOUNT_READ = "account_read"
    PERMISSION_PROFILE_READ = "permission_profile_read"
    LAUNCH = "launch"
    READ = "read"
    SYNC = "sync"
    TRANSCRIPT = "transcript"
    TAIL = "tail"
    SEND = "send"
    QUEUE_NATIVE = "queue_native"
    QUEUE_EVIDENCE = "queue_evidence"
    STEER = "steer"
    INJECT_CONTEXT = "inject_context"
    INTERRUPT = "interrupt"
    GOAL_READ = "goal_read"
    GOAL_WRITE = "goal_write"
    FORK = "fork"
    ROLLBACK = "rollback"
    COMPACT = "compact"
    RENAME = "rename"
    ARCHIVE = "archive"
    RESTORE = "restore"
    TOPOLOGY = "topology"
    DISCOVER = "discover"
    SEARCH = "search"
    EVENT_STREAM = "event_stream"
    SERVER_REQUEST_STREAM = "server_request_stream"
    SERVER_REQUEST_RESPONSE = "server_request_response"


ALL_CODEX_ACTIONS = frozenset(ProviderAction)


@dataclass(frozen=True)
class ProviderTarget:
    lane_id: str
    provider: str
    binding_id: str
    native_session_id: str


@dataclass(frozen=True)
class PreparedProviderRequest:
    """One immutable provider call persisted before submission."""

    target: ProviderTarget
    action: ProviderAction
    transport: DeliveryTransport
    correlation_id: str
    text: str
    cwd: str | None = None
    settings: TurnStartSettings | None = None


@dataclass(frozen=True)
class ProviderSubmissionAccepted:
    status: Literal["accepted"] = "accepted"
    submission_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class ProviderSubmissionRejected:
    error: str
    status: Literal["rejected"] = "rejected"


@dataclass(frozen=True)
class ProviderSubmissionUnknown:
    error: str
    status: Literal["unknown"] = "unknown"


ProviderSubmissionResult = (
    ProviderSubmissionAccepted | ProviderSubmissionRejected | ProviderSubmissionUnknown
)


@dataclass(frozen=True)
class ProviderAvailability:
    ready: bool
    reason: str | None = None
    generation: str | None = None


@dataclass(frozen=True)
class ProviderDurability:
    local_reservation: bool = False
    provider_idempotency: bool = False
    native_evidence: bool = False


@dataclass(frozen=True)
class ProviderBindingFacts:
    provider: str
    binding_id: str
    supported_actions: frozenset[ProviderAction]
    availability: ProviderAvailability
    durability: ProviderDurability

    def supports(self, action: ProviderAction) -> bool:
        return action in self.supported_actions


class CodexLaneAdapter:
    """Operation-shaped wrapper around one exact Codex App Server binding."""

    def __init__(
        self,
        client: LaneClient,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        availability: ProviderAvailability | None = None,
        supported_actions: frozenset[ProviderAction] = ALL_CODEX_ACTIONS,
        durability: ProviderDurability | None = None,
    ) -> None:
        self.client = client
        self.facts = ProviderBindingFacts(
            provider="codex",
            binding_id=binding_id,
            supported_actions=supported_actions,
            availability=availability or ProviderAvailability(ready=True),
            durability=durability or ProviderDurability(),
        )

    def _native(self, target: ProviderTarget) -> str:
        if target.provider != self.facts.provider or target.binding_id != self.facts.binding_id:
            raise CapabilityUnavailableError(
                "provider route target does not match its fixed adapter binding"
            )
        return target.native_session_id

    async def config_read(self) -> ConfigInfo:
        return await self.client.config_read()

    async def model_list(self) -> list[AppModel]:
        return await self.client.model_list()

    async def account_read(self) -> AccountReadResult:
        return await self.client.account_read()

    async def account_rate_limits_read(self) -> AccountRateLimitsResult:
        return await self.client.account_rate_limits_read()

    async def account_usage_read(self) -> AccountUsageResult:
        return await self.client.account_usage_read()

    async def permission_profile_list(
        self, *, cwd: str | None = None, limit: int | None = None
    ) -> list[PermissionProfileSummary]:
        return await self.client.permission_profile_list(cwd=cwd, limit=limit)

    async def read(self, target: ProviderTarget, *, include_turns: bool) -> dict[str, object]:
        return await self.client.thread_read(self._native(target), include_turns=include_turns)

    async def resume(
        self,
        target: ProviderTarget,
        *,
        permission_profile: str | None = None,
        exclude_turns: bool | None = None,
        initial_turns_page: ThreadResumeInitialTurnsPageParams | None = None,
    ) -> ThreadInfo:
        return await self.client.thread_resume(
            self._native(target),
            permission_profile=permission_profile,
            exclude_turns=exclude_turns,
            initial_turns_page=initial_turns_page,
        )

    async def resume_full(
        self,
        target: ProviderTarget,
        *,
        permission_profile: str | None = None,
        exclude_turns: bool | None = None,
        initial_turns_page: ThreadResumeInitialTurnsPageParams | None = None,
    ) -> ThreadResumeResult:
        return await self.client.thread_resume_full(
            self._native(target),
            permission_profile=permission_profile,
            exclude_turns=exclude_turns,
            initial_turns_page=initial_turns_page,
        )

    async def turns_list(
        self,
        target: ProviderTarget,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: SortDirection | None = None,
        items_view: TurnItemsView | None = None,
    ) -> ThreadTurnsPage:
        return await self.client.thread_turns_list(
            self._native(target),
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            items_view=items_view,
        )

    async def items_list(
        self,
        target: ProviderTarget,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: SortDirection | None = None,
        turn_id: str | None = None,
    ) -> ThreadItemsPage:
        return await self.client.thread_items_list(
            self._native(target),
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            turn_id=turn_id,
        )

    async def start_turn(
        self,
        target: ProviderTarget,
        text: str,
        *,
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
        client_user_message_id: str | None = None,
    ) -> dict[str, object]:
        return await self.client.turn_start(
            self._native(target),
            text,
            cwd=cwd,
            input_items=input_items,
            permission_profile=permission_profile,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            sandbox_policy=sandbox_policy,
            effort=effort,
            summary=summary,
            model=model,
            service_tier=service_tier,
            output_schema=output_schema,
            personality=personality,
            client_user_message_id=client_user_message_id,
        )

    async def steer(
        self,
        target: ProviderTarget,
        expected_turn_id: str,
        text: str,
        *,
        input_items: list[UserInput] | None = None,
    ) -> dict[str, object]:
        return await self.client.turn_steer(
            self._native(target), expected_turn_id, text, input_items=input_items
        )

    async def inject(self, target: ProviderTarget, items: list[dict[str, object]]) -> None:
        await self.client.inject_items(self._native(target), items)

    async def interrupt(self, target: ProviderTarget, turn_id: str) -> None:
        await self.client.turn_interrupt(self._native(target), turn_id)

    async def queue_add(
        self, target: ProviderTarget, text: str, *, client_user_message_id: str
    ) -> QueuedSubmission:
        return await self.client.thread_queue_add(
            self._native(target), text, client_user_message_id=client_user_message_id
        )

    async def submit_prepared(self, request: PreparedProviderRequest) -> ProviderSubmissionResult:
        """Submit one frozen Codex request and classify only admission evidence."""

        try:
            async with asyncio.timeout(15):
                if request.transport == "native_queue":
                    if request.action != ProviderAction.QUEUE_NATIVE:
                        raise ValueError("native queue request has the wrong provider action")
                    submission = await self.queue_add(
                        request.target,
                        request.text,
                        client_user_message_id=request.correlation_id,
                    )
                    if (
                        submission.client_user_message_id != request.correlation_id
                        or len(submission.input) != 1
                        or submission.input[0].type != "text"
                        or submission.input[0].text != request.text
                    ):
                        raise ProtocolError(
                            "native queue acknowledgment does not match the reserved input"
                        )
                    return ProviderSubmissionAccepted(submission_id=submission.id)

                if request.action != ProviderAction.SEND:
                    raise ValueError("turn request has the wrong provider action")
                if request.cwd is None or request.settings is None:
                    raise ValueError("turn request is missing frozen cwd or settings")
                settings = request.settings
                result = await self.start_turn(
                    request.target,
                    request.text,
                    cwd=request.cwd,
                    client_user_message_id=request.correlation_id,
                    permission_profile=settings.permission_profile,
                    approval_policy=settings.approval_policy,
                    approvals_reviewer=settings.approvals_reviewer,
                    sandbox_policy=settings.sandbox_policy,
                    effort=settings.effort,
                    summary=settings.summary,
                    model=settings.model,
                    service_tier=settings.service_tier,
                    output_schema=settings.output_schema,
                    personality=settings.personality,
                )
                turn = result.get("turn")
                turn_id = turn.get("id") if isinstance(turn, dict) else None
                if not isinstance(turn_id, str):
                    turn_id = result.get("turnId")
                submission_id = result.get("submissionId")
                return ProviderSubmissionAccepted(
                    submission_id=submission_id if isinstance(submission_id, str) else None,
                    turn_id=turn_id if isinstance(turn_id, str) else None,
                )
        except AppServerError as exc:
            if exc.code in {-32600, -32601, -32602}:
                return ProviderSubmissionRejected(error=str(exc))
            return ProviderSubmissionUnknown(error=str(exc))
        except (ClientError, TimeoutError) as exc:
            return ProviderSubmissionUnknown(error=str(exc))

    async def queue_list(
        self, target: ProviderTarget, *, cursor: str | None = None, limit: int | None = None
    ) -> ThreadQueuePage:
        return await self.client.thread_queue_list(self._native(target), cursor=cursor, limit=limit)

    def raw_events(self, target: ProviderTarget) -> AsyncIterator[dict[str, object]]:
        return self.client.raw_events(self._native(target))

    def events(self) -> AsyncIterator[LaneEvent]:
        return self.client.events(None)

    def account_events(self) -> AsyncIterator[AccountRateLimitsUpdated]:
        return self.client.account_events()

    async def goal_get(self, target: ProviderTarget) -> ThreadGoal | None:
        return await self.client.thread_goal_get(self._native(target))

    async def goal_set(
        self,
        target: ProviderTarget,
        *,
        objective: str | None = None,
        status: ThreadGoalStatus | None = None,
        token_budget: int | None = None,
    ) -> ThreadGoal:
        return await self.client.thread_goal_set(
            self._native(target),
            objective=objective,
            status=status,
            token_budget=token_budget,
        )

    async def goal_clear(self, target: ProviderTarget) -> None:
        await self.client.thread_goal_clear(self._native(target))

    async def fork(
        self,
        target: ProviderTarget,
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
        return await self.client.thread_fork(
            self._native(target),
            cwd=cwd,
            permission_profile=permission_profile,
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

    async def rollback(self, target: ProviderTarget, num_turns: int) -> ThreadInfo:
        return await self.client.thread_rollback(self._native(target), num_turns)

    async def compact(self, target: ProviderTarget) -> None:
        await self.client.thread_compact_start(self._native(target))

    async def rename(self, target: ProviderTarget, name: str) -> None:
        await self.client.thread_set_name(self._native(target), name)

    async def archive(self, target: ProviderTarget) -> None:
        await self.client.thread_archive(self._native(target))

    async def restore(self, target: ProviderTarget) -> ThreadInfo:
        return await self.client.thread_unarchive(self._native(target))

    async def start_thread(
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
        return await self.client.thread_start(
            cwd=cwd,
            permission_profile=permission_profile,
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

    async def list_threads(
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
        return await self.client.thread_list(
            limit=limit,
            cursor=cursor,
            use_state_db_only=use_state_db_only,
            archived=archived,
            cwd=cwd,
            model_providers=model_providers,
            search_term=search_term,
            sort_direction=sort_direction,
            sort_key=sort_key,
            source_kinds=source_kinds,
            parent_thread_id=parent_thread_id,
            ancestor_thread_id=ancestor_thread_id,
        )

    async def search_threads(
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
        return await self.client.thread_search(
            search_term,
            archived=archived,
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            sort_key=sort_key,
            source_kinds=source_kinds,
        )

    def server_requests(self) -> AsyncIterator[ServerRequestReceived]:
        return self.client.server_requests(None)

    async def respond_server_request(
        self,
        request_id: JsonRpcId,
        *,
        result: Mapping[str, object] | None = None,
        error: JsonRpcError | None = None,
    ) -> None:
        await self.client.respond_server_request(request_id, result=result, error=error)

    async def respond_approval(self, request_id: JsonRpcId, decision: Decision) -> None:
        await self.client.respond_approval(request_id, decision)


class BoundCodexHistoryClient:
    """History-only client fixed to one already-routed lane target."""

    def __init__(self, route: ProviderRoute, current_generation: Callable[[], str | None]) -> None:
        self._route = route
        self._current_generation = current_generation

    def _check(self, thread_id: str) -> None:
        if thread_id != self._route.target.native_session_id:
            raise CapabilityUnavailableError("history call attempted to change its routed target")
        self._route.recheck(self._current_generation())

    async def thread_resume(
        self,
        thread_id: str,
        *,
        permission_profile: str | None = None,
        exclude_turns: bool | None = None,
        initial_turns_page: ThreadResumeInitialTurnsPageParams | None = None,
    ) -> ThreadInfo:
        self._check(thread_id)
        return await self._route.adapter.resume(
            self._route.target,
            permission_profile=permission_profile,
            exclude_turns=exclude_turns,
            initial_turns_page=initial_turns_page,
        )

    async def thread_resume_full(
        self,
        thread_id: str,
        *,
        permission_profile: str | None = None,
        exclude_turns: bool | None = None,
        initial_turns_page: ThreadResumeInitialTurnsPageParams | None = None,
    ) -> ThreadResumeResult:
        self._check(thread_id)
        return await self._route.adapter.resume_full(
            self._route.target,
            permission_profile=permission_profile,
            exclude_turns=exclude_turns,
            initial_turns_page=initial_turns_page,
        )

    async def thread_turns_list(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: SortDirection | None = None,
        items_view: TurnItemsView | None = None,
    ) -> ThreadTurnsPage:
        self._check(thread_id)
        return await self._route.adapter.turns_list(
            self._route.target,
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            items_view=items_view,
        )

    async def thread_items_list(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: SortDirection | None = None,
        turn_id: str | None = None,
    ) -> ThreadItemsPage:
        self._check(thread_id)
        return await self._route.adapter.items_list(
            self._route.target,
            cursor=cursor,
            limit=limit,
            sort_direction=sort_direction,
            turn_id=turn_id,
        )


@dataclass(frozen=True)
class ProviderRoute:
    target: ProviderTarget
    action: ProviderAction
    adapter: CodexLaneAdapter
    availability: ProviderAvailability
    durability: ProviderDurability

    def history_client(
        self, current_generation: Callable[[], str | None]
    ) -> BoundCodexHistoryClient:
        return BoundCodexHistoryClient(self, current_generation)

    def recheck(self, generation: str | None) -> None:
        if not self.availability.ready:
            raise CapabilityUnavailableError(
                self.availability.reason
                or (
                    f"provider binding {self.target.provider}:"
                    f"{self.target.binding_id} is unavailable"
                )
            )
        expected = self.availability.generation
        if expected is not None and generation != expected:
            raise CapabilityUnavailableError(
                f"provider binding {self.target.provider}:{self.target.binding_id} "
                "connection generation changed"
            )


@dataclass(frozen=True)
class ProviderLaunchRoute:
    provider: str
    binding_id: str
    action: ProviderAction
    adapter: CodexLaneAdapter
    availability: ProviderAvailability
    durability: ProviderDurability

    def recheck(self, generation: str | None) -> None:
        if not self.availability.ready:
            raise CapabilityUnavailableError(
                self.availability.reason
                or f"provider binding {self.provider}:{self.binding_id} is unavailable"
            )
        expected = self.availability.generation
        if expected is not None and generation != expected:
            raise CapabilityUnavailableError(
                f"provider binding {self.provider}:{self.binding_id} connection generation changed"
            )


class ProviderRouter:
    """Resolve exact provider bindings without implicit fallback."""

    def __init__(self, adapters: tuple[CodexLaneAdapter, ...]) -> None:
        self._adapters = {
            (adapter.facts.provider, adapter.facts.binding_id): adapter for adapter in adapters
        }
        if len(self._adapters) != len(adapters):
            raise ValueError("provider adapters must have unique provider/binding identities")

    @classmethod
    def default_codex(cls, client: LaneClient, *, generation: str | None = None) -> ProviderRouter:
        return cls(
            (
                CodexLaneAdapter(
                    client,
                    availability=ProviderAvailability(ready=True, generation=generation),
                ),
            )
        )

    def facts_for_lane(self, lane: Lane) -> ProviderBindingFacts | None:
        adapter = self._adapters.get((lane.provider, lane.binding_id))
        return adapter.facts if adapter is not None else None

    def route_lane(self, lane: Lane, action: ProviderAction) -> ProviderRoute:
        native_id = lane.provider_session_id
        if native_id is None:
            raise CapabilityUnavailableError(
                f"lane {lane.id!r} has no provider session identity for {action.value}"
            )
        target = ProviderTarget(
            lane_id=lane.id,
            provider=lane.provider,
            binding_id=lane.binding_id,
            native_session_id=native_id,
        )
        return self.route_target(target, action)

    def route_target(self, target: ProviderTarget, action: ProviderAction) -> ProviderRoute:
        """Route an already frozen target without resolving mutable lane metadata."""

        adapter = self._adapters.get((target.provider, target.binding_id))
        if adapter is None:
            raise CapabilityUnavailableError(
                f"provider binding {target.provider}:{target.binding_id} execution is not "
                "supported because the binding is not registered"
            )
        if not adapter.facts.supports(action):
            raise CapabilityUnavailableError(
                f"{action.value} is unsupported by provider binding "
                f"{target.provider}:{target.binding_id}"
            )
        if (
            target.provider == "codex"
            and target.binding_id == DEFAULT_CODEX_BINDING_ID
            and target.native_session_id != target.lane_id
        ):
            raise CapabilityUnavailableError(
                "default-Codex provider session identity does not match the stable lane id"
            )
        availability = adapter.facts.availability
        if not availability.ready:
            raise CapabilityUnavailableError(
                availability.reason
                or f"provider binding {target.provider}:{target.binding_id} is unavailable"
            )
        return ProviderRoute(
            target=target,
            action=action,
            adapter=adapter,
            availability=availability,
            durability=adapter.facts.durability,
        )

    def route_binding(
        self, provider: str, binding_id: str, action: ProviderAction
    ) -> ProviderLaunchRoute:
        adapter = self._adapters.get((provider, binding_id))
        if adapter is None:
            raise CapabilityUnavailableError(
                f"provider binding {provider}:{binding_id} is not registered"
            )
        if not adapter.facts.supports(action):
            raise CapabilityUnavailableError(
                f"{action.value} is unsupported by provider binding {provider}:{binding_id}"
            )
        availability = adapter.facts.availability
        if not availability.ready:
            raise CapabilityUnavailableError(
                availability.reason or f"provider binding {provider}:{binding_id} is unavailable"
            )
        return ProviderLaunchRoute(
            provider=provider,
            binding_id=binding_id,
            action=action,
            adapter=adapter,
            availability=availability,
            durability=adapter.facts.durability,
        )

    def route_session(
        self,
        provider: str,
        binding_id: str,
        native_session_id: str,
        action: ProviderAction,
    ) -> ProviderRoute:
        binding = self.route_binding(provider, binding_id, action)
        return ProviderRoute(
            target=ProviderTarget(
                lane_id=native_session_id,
                provider=provider,
                binding_id=binding_id,
                native_session_id=native_session_id,
            ),
            action=action,
            adapter=binding.adapter,
            availability=binding.availability,
            durability=binding.durability,
        )

    def route_launch(self, provider: str | None, action: ProviderAction) -> ProviderLaunchRoute:
        resolved_provider = provider or "codex"
        if resolved_provider != "codex":
            raise CapabilityUnavailableError(
                f"provider {resolved_provider!r} has no registered launch binding"
            )
        return self.route_binding("codex", DEFAULT_CODEX_BINDING_ID, action)


def router_for(ctx: Ctx) -> ProviderRouter:
    if ctx.providers is not None:
        return ctx.providers
    return ProviderRouter.default_codex(ctx.client, generation=ctx.provider_session_id or None)


def route_lane(ctx: Ctx, lane: Lane, action: ProviderAction) -> ProviderRoute:
    return router_for(ctx).route_lane(lane, action)
