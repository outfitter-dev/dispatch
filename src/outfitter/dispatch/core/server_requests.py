"""Durable policy and response loop for App Server interactive requests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from outfitter.dispatch.client.events import ServerRequestReceived
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import NotFoundError, ValidationError
from outfitter.dispatch.registry.models import (
    Lane,
    LaneRuntimeState,
    ProviderEvent,
    ServerRequest,
)

from .server_request_policy import (
    PlannedResponse,
    automatic_response,
    denied_response,
    expected_response,
    timeout_response,
    validate_operator_response,
)
from .subscriptions import process_server_request_subscriptions


class ServerRequestManager:
    """Consume every server request and give it one durable outcome."""

    def __init__(
        self,
        ctx: Ctx,
        *,
        on_approval_attention: Callable[[str], Awaitable[object]] | None = None,
    ) -> None:
        self._ctx = ctx
        self._on_approval_attention = on_approval_attention
        if not ctx.provider_session_id:
            ctx.provider_session_id = uuid4().hex
        self._timeouts: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        stale_open = await self._ctx.registry.list_open_server_requests_except_session(
            self._ctx.provider_session_id
        )
        recovered = await self._ctx.registry.fail_open_server_requests_except_session(
            self._ctx.provider_session_id
        )
        if recovered:
            self._ctx.log.warning("server_request.recovered_stale", count=recovered)
            lanes: set[str] = set()
            for stale_request in stale_open:
                if stale_request.id is None:
                    continue
                failed = await self._ctx.registry.get_server_request_by_id(stale_request.id)
                if failed is not None:
                    await _record_request_event(self._ctx, failed, "failed")
                    if failed.lane is not None:
                        lanes.add(failed.lane)
            for lane_id in lanes:
                await _clear_attention_if_resolved(self._ctx, lane_id)
        try:
            async for incoming in self._ctx.client.server_requests(None):
                try:
                    await self.handle(incoming)
                except Exception:
                    self._ctx.log.exception(
                        "server_request.handle_failed",
                        method=incoming.method,
                        lane=incoming.lane_id,
                    )
        finally:
            await self.close()

    async def close(self) -> None:
        for task in self._timeouts:
            task.cancel()
        if self._timeouts:
            await asyncio.gather(*self._timeouts, return_exceptions=True)
        self._timeouts.clear()

    async def handle(self, request: ServerRequestReceived) -> ServerRequest:
        lane = await self._ctx.registry.find_lane(request.lane_id) if request.lane_id else None
        now = datetime.now(UTC)
        received_at = now.isoformat()
        timeout = self._ctx.policy.interactive_request_timeout_seconds
        observation = await self._ctx.registry.observe_server_request_once(
            ServerRequest(
                provider_session_id=self._ctx.provider_session_id,
                provider_thread_id=request.lane_id,
                lane=lane.id if lane is not None else None,
                request_id=request.request_id,
                method=request.method,
                category=request.category,
                received_at=received_at,
                deadline_at=(now + timedelta(seconds=timeout)).isoformat(),
            )
        )
        stored = observation.request
        if not observation.inserted:
            self._ctx.log.info(
                "server_request.duplicate",
                request_id=stored.id,
                method=stored.method,
                state=stored.state,
            )
            return stored
        await _record_request_event(self._ctx, stored, "received")
        mode = (
            self._ctx.policy.owned_interactive_requests
            if lane is not None and lane.source == "own"
            else self._ctx.policy.attached_interactive_requests
        )
        plan = automatic_response(request, mode=mode, actionable=lane is not None)
        if plan is not None:
            await _send_response(self._ctx, stored, plan)
            return (await self._ctx.registry.get_server_request_by_id(_local_id(stored))) or stored

        await _surface_attention(self._ctx, stored, lane, request)
        if (
            request.category == "approval"
            and lane is not None
            and self._on_approval_attention is not None
        ):
            await self._on_approval_attention(lane.id)
        task = asyncio.create_task(self._expire_after(_local_id(stored), timeout))
        self._timeouts.add(task)
        task.add_done_callback(self._timeouts.discard)
        return stored

    async def _expire_after(self, request_id: int, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await self.expire(request_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._ctx.log.exception("server_request.timeout_response_failed", request_id=request_id)

    async def expire(self, request_id: int) -> None:
        """Apply the category-safe timeout response if the request is still pending."""
        request = await self._ctx.registry.get_server_request_by_id(request_id)
        if request is None or request.state != "pending":
            return
        await _send_response(self._ctx, request, timeout_response(request.method))


async def respond_to_server_request(
    ctx: Ctx, request_id: int, response: Mapping[str, object]
) -> ServerRequest:
    request = await ctx.registry.get_server_request_by_id(request_id)
    if request is None:
        raise NotFoundError(f"no interactive request {request_id!r}")
    if request.provider_session_id != ctx.provider_session_id:
        raise ValidationError("interactive request belongs to a closed App Server connection")
    if request.state != "pending":
        raise ValidationError(f"interactive request is already {request.state}")
    validated = validate_operator_response(request.method, response)
    sent = await _send_response(
        ctx,
        request,
        PlannedResponse(result=validated, summary="operator responded"),
    )
    if not sent:
        current = await ctx.registry.get_server_request_by_id(request_id)
        state = current.state if current is not None else "unavailable"
        raise ValidationError(f"interactive request is already {state}")
    result = await ctx.registry.get_server_request_by_id(request_id)
    if result is None:
        raise RuntimeError("responded interactive request disappeared")
    return result


async def _send_response(ctx: Ctx, request: ServerRequest, plan: PlannedResponse) -> bool:
    local_id = _local_id(request)
    claimed = await ctx.registry.claim_server_request_by_id(local_id)
    if claimed is None:
        return False
    try:
        await ctx.client.respond_server_request(
            claimed.request_id,
            result=plan.result,
            error=plan.error,
        )
    except Exception as exc:
        failed = await ctx.registry.finalize_server_request_by_id(
            local_id,
            state="failed",
            error=str(exc),
        )
        if failed is not None:
            await _record_request_event(ctx, failed, "failed")
        await ctx.registry.log_action(
            "server-request",
            lane=claimed.lane,
            detail=f"{local_id}:{claimed.method}:send failed",
            outcome="error",
        )
        if claimed.lane is not None:
            await _clear_attention_if_resolved(ctx, claimed.lane)
        raise
    finalized = await ctx.registry.finalize_server_request_by_id(
        local_id,
        state=plan.state,
        response_summary=plan.summary,
    )
    if finalized is not None:
        await _record_request_event(ctx, finalized, plan.state)
    await ctx.registry.log_action(
        "server-request",
        lane=claimed.lane,
        detail=f"{local_id}:{claimed.method}:{plan.summary}",
        outcome="ok" if plan.state == "responded" else plan.state,
    )
    if claimed.lane is not None:
        await _clear_attention_if_resolved(ctx, claimed.lane)
    return True


async def _surface_attention(
    ctx: Ctx,
    stored: ServerRequest,
    lane: Lane | None,
    request: ServerRequestReceived,
) -> None:
    local_id = _local_id(stored)
    if lane is None:
        await _send_response(ctx, stored, denied_response(request.method))
        return
    status = _waiting_status(request.category)
    await ctx.registry.update_lane_status(lane.id, status)
    now = datetime.now(UTC).isoformat()
    current = await ctx.registry.get_lane_runtime_state(lane.id)
    await ctx.registry.upsert_lane_runtime_state(
        LaneRuntimeState(
            lane=lane.id,
            provider="codex",
            provider_thread_id=lane.id,
            status=status,
            active_turn_id=(current.active_turn_id if current else lane.active_turn_id),
            latest_turn_id=(current.latest_turn_id if current else lane.latest_turn_id),
            latest_turn_status=(current.latest_turn_status if current else lane.latest_turn_status),
            needs_attention=True,
            attention_kind=request.category,
            attention_detail=f"request {local_id}: {request.method}",
            updated_at=now,
            last_event_at=now,
        )
    )
    expected = expected_response(request.method)
    payload: dict[str, object] = {
        "request_id": local_id,
        "method": request.method,
        "category": request.category,
        "expected_response": expected,
    }
    if request.category == "user_input":
        payload["questions"] = _safe_questions(request.raw_params.get("questions"))
    elif request.category == "elicitation":
        server_name = request.raw_params.get("serverName")
        if isinstance(server_name, str):
            payload["server_name"] = server_name[:256]
    response_example = json.dumps(expected or {}, separators=(",", ":"))
    await ctx.registry.add_inbox_message(
        recipient_lane=lane.id,
        kind="system_notice",
        subject=f"Interactive request {local_id}: {request.category}",
        body=(
            f"{request.method} is waiting for an operator response. "
            f"Use `dispatch request respond {local_id} '{response_example}'`."
        ),
        payload=payload,
        delivery="inbox",
    )
    await process_server_request_subscriptions(
        ctx,
        lane,
        request_id=local_id,
        category=request.category,
        method=request.method,
    )
    await ctx.registry.log_action(
        "server-request",
        lane=lane.id,
        detail=f"{local_id}:{request.method}:waiting",
    )


async def _clear_attention_if_resolved(ctx: Ctx, lane_id: str) -> None:
    if await ctx.registry.list_server_requests(state="pending", lane=lane_id, limit=1):
        return
    lane = await ctx.registry.get_lane(lane_id)
    now = datetime.now(UTC).isoformat()
    current = await ctx.registry.get_lane_runtime_state(lane_id)
    active_turn_id = current.active_turn_id if current else lane.active_turn_id
    status: Literal["busy", "idle"] = "busy" if active_turn_id else "idle"
    await ctx.registry.update_lane_status(lane_id, status)
    await ctx.registry.upsert_lane_runtime_state(
        LaneRuntimeState(
            lane=lane_id,
            provider="codex",
            provider_thread_id=lane_id,
            status=status,
            active_turn_id=active_turn_id,
            latest_turn_id=(current.latest_turn_id if current else lane.latest_turn_id),
            latest_turn_status=(current.latest_turn_status if current else lane.latest_turn_status),
            needs_attention=False,
            updated_at=now,
            last_event_at=(current.last_event_at if current else now),
        )
    )


def _waiting_status(
    category: str,
) -> Literal["waiting_approval", "waiting_input", "waiting_elicitation", "waiting_tool"]:
    if category == "approval":
        return "waiting_approval"
    if category == "user_input":
        return "waiting_input"
    if category == "elicitation":
        return "waiting_elicitation"
    return "waiting_tool"


def _safe_questions(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    questions: list[dict[str, object]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        question: dict[str, object] = {}
        for key in ("id", "header", "question"):
            field = item.get(key)
            if isinstance(field, str):
                question[key] = field[:1024]
        options = item.get("options")
        if isinstance(options, list):
            safe_options: list[dict[str, str]] = []
            for option in options[:20]:
                if not isinstance(option, dict):
                    continue
                safe_option: dict[str, str] = {}
                for key in ("label", "description"):
                    field = option.get(key)
                    if isinstance(field, str):
                        safe_option[key] = field[:512]
                if safe_option:
                    safe_options.append(safe_option)
            question["options"] = safe_options
        questions.append(question)
    return questions


def _local_id(request: ServerRequest) -> int:
    if request.id is None:
        raise RuntimeError("persisted server request has no local id")
    return request.id


async def _record_request_event(ctx: Ctx, request: ServerRequest, state: str) -> None:
    if request.provider_thread_id is None:
        return
    local_id = _local_id(request)
    now = datetime.now(UTC).isoformat()
    await ctx.registry.record_provider_event(
        ProviderEvent(
            provider="codex",
            provider_thread_id=request.provider_thread_id,
            lane=request.lane,
            event_type=f"server_request.{state}",
            provider_event_id=(f"server-request:{request.provider_session_id}:{local_id}:{state}"),
            correlation_id=f"server-request:{local_id}",
            received_at=now,
            summary={
                "request_id": local_id,
                "method": request.method,
                "category": request.category,
                "state": state,
            },
        )
    )
