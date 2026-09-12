"""Reserved text deliveries; provider acceptance is not execution completion."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from uuid import uuid4

from pydantic import TypeAdapter

from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import (
    AuthorityError,
    CapabilityUnavailableError,
    DeliveryConflictError,
    DispatchError,
    NotFoundError,
    ValidationError,
)
from outfitter.dispatch.registry.delivery import DeliveryReceipt
from outfitter.dispatch.registry.models import Lane
from outfitter.dispatch.registry.observations import ProviderCorrelation, ProviderObservation
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID

from .models import DeliveryLookupInput, DeliveryView, SendInput
from .providers import (
    PreparedProviderRequest,
    ProviderAction,
    ProviderSubmissionAccepted,
    ProviderSubmissionRejected,
    ProviderTarget,
    route_lane,
    router_for,
)
from .turn_settings import TurnStartSettings, load_turn_start_settings

_SETTINGS = TypeAdapter(TurnStartSettings)
_PREPARED = TypeAdapter(PreparedProviderRequest)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def submitted_payload(inp: SendInput) -> str:
    """Canonical caller intent, captured before mutable resolution."""

    return _canonical_json(
        {
            "version": 1,
            "lane": inp.lane,
            "text": inp.text,
            "content": [item.model_dump(mode="json") for item in inp.content],
            "mode": inp.mode,
            "intro": inp.intro,
            "caller_thread_id": inp.caller_thread_id if inp.intro else None,
        }
    )


def encode_prepared_request(request: PreparedProviderRequest) -> str:
    return _canonical_json(
        {
            "version": 1,
            # Retained at top level for the bounded v23 reconciliation reader.
            "text": request.text,
            "request": _PREPARED.dump_python(request, mode="json", exclude_none=True),
        }
    )


def decode_prepared_request(receipt: DeliveryReceipt) -> PreparedProviderRequest:
    payload = json.loads(receipt.payload)
    if payload.get("version") == 1 and isinstance(payload.get("request"), dict):
        return _PREPARED.validate_python(payload["request"])

    # Pre-v25 receipts were created only for the default Codex binding. Their
    # stable lane id was also the native Codex session id.
    settings = None
    cwd = None
    if receipt.transport == "turn":
        settings = _SETTINGS.validate_python(payload["settings"])
        cwd = str(payload["cwd"])
    return PreparedProviderRequest(
        target=ProviderTarget(
            lane_id=receipt.lane,
            provider="codex",
            binding_id=DEFAULT_CODEX_BINDING_ID,
            native_session_id=receipt.lane,
        ),
        action=(
            ProviderAction.QUEUE_NATIVE
            if receipt.transport == "native_queue"
            else ProviderAction.SEND
        ),
        transport=receipt.transport,
        correlation_id=receipt.id,
        text=str(payload["text"]),
        cwd=cwd,
        settings=settings,
    )


def _legacy_replay_matches(inp: SendInput, receipt: DeliveryReceipt) -> bool:
    """Recognize only legacy retries whose original intent is still provable."""

    if inp.lane != receipt.lane or inp.mode != receipt.mode or inp.intro or inp.content:
        return False
    try:
        return decode_prepared_request(receipt).text == (inp.text or "")
    except (KeyError, TypeError, ValueError):
        return False


async def find_replay(inp: SendInput, ctx: Ctx) -> tuple[DeliveryView, Lane] | None:
    """Return an exact prior reservation before resolving any mutable input."""

    if inp.idempotency_key is None:
        return None
    binding = await ctx.registry.get_caller_key_binding(inp.idempotency_key)
    if binding is None:
        return None
    kind, record = binding
    if kind == "launch":
        raise DeliveryConflictError(
            f"delivery key {inp.idempotency_key!r} is already bound to a lane launch"
        )
    assert isinstance(record, DeliveryReceipt)
    receipt = record
    matches = (
        receipt.submitted_payload == submitted_payload(inp)
        if receipt.submitted_payload is not None
        else _legacy_replay_matches(inp, receipt)
    )
    if not matches:
        detail = (
            "legacy delivery keys can replay only with the stable thread id and original "
            "plain-text options"
            if receipt.submitted_payload is None
            else "delivery key is already bound to different submitted input"
        )
        raise DeliveryConflictError(f"delivery key {inp.idempotency_key!r}: {detail}")
    lane = await ctx.registry.find_lane(receipt.lane)
    if lane is None:
        raise NotFoundError(f"no managed thread {receipt.lane!r}")
    return DeliveryView.model_validate(receipt.model_dump(mode="json")), lane


async def get_receipt(inp: DeliveryLookupInput, ctx: Ctx) -> DeliveryView:
    receipt = await ctx.registry.get_delivery(inp.receipt_id)
    return DeliveryView.model_validate(receipt.model_dump(mode="json"))


async def reconcile_receipt_request(inp: DeliveryLookupInput, ctx: Ctx) -> DeliveryView:
    from .delivery_reconciliation import reconcile_receipt

    await reconcile_receipt(inp.receipt_id, ctx, automatic=False)
    return await get_receipt(inp, ctx)


async def send_reserved(inp: SendInput, lane: Lane, text: str, ctx: Ctx) -> DeliveryView:
    native = lane.source == "attached" and inp.mode == "queue"
    action = ProviderAction.QUEUE_NATIVE if native else ProviderAction.SEND
    if lane.provider == "hermes":
        runtime = await ctx.registry.get_lane_runtime_state(lane.id)
        if runtime is not None and runtime.needs_attention:
            raise CapabilityUnavailableError(
                "Hermes thread has an unresolved native attention hold; no submission was attempted"
            )
        if await ctx.registry.lane_delivery_held(lane.id):
            raise CapabilityUnavailableError(
                "Hermes thread has an unresolved delivery; no submission was attempted"
            )
        launch = await ctx.registry.get_lane_launch(lane.id)
        if (
            launch.status != "created"
            or launch.runtime_session_id is None
            or launch.stored_session_id is None
        ):
            raise CapabilityUnavailableError(
                "Hermes lane creation is incomplete or held; no submission was attempted"
            )
        target = ProviderTarget(
            lane_id=lane.id,
            provider=lane.provider,
            binding_id=lane.binding_id,
            native_session_id=launch.stored_session_id,
            runtime_session_id=launch.runtime_session_id,
            generation=launch.generation,
        )
        route = router_for(ctx).route_submission_target(target, action)
        assert target.generation is not None
        route.recheck_generation(target.generation)
    else:
        route = router_for(ctx).route_submission_target(
            route_lane(ctx, lane, action).target, action
        )
    route.recheck()
    if lane.source != "own" and not native:
        raise AuthorityError("idempotent delivery currently requires a Dispatch-owned thread")
    if native and inp.content:
        raise ValidationError("native attached queue currently supports plain text only")
    mode = inp.mode
    if mode != "send" and mode != "queue":
        raise ValidationError("idempotent delivery supports send or queue")
    settings = (
        None
        if native or lane.provider == "hermes"
        else await load_turn_start_settings(ctx.registry, lane.id)
    )
    delivery_id = str(uuid4())
    request = PreparedProviderRequest(
        target=route.target,
        action=ProviderAction.QUEUE_NATIVE if native else ProviderAction.SEND,
        transport="native_queue" if native else "turn",
        correlation_id=delivery_id,
        text=text,
        cwd=None if native else lane.cwd or ".",
        settings=settings,
    )
    receipt, created = await ctx.registry.reserve_delivery(
        key=inp.idempotency_key,
        lane=lane.id,
        mode=mode,
        submitted_payload=submitted_payload(inp),
        payload=encode_prepared_request(request),
        text=text,
        delivery_id=delivery_id,
        transport="native_queue" if native else "turn",
        provider=request.target.provider,
        binding_id=request.target.binding_id,
        native_session_id=request.target.native_session_id,
        correlation_id=request.correlation_id,
    )
    if created:
        await ctx.registry.log_action(
            mode, lane=lane.id, detail=f"delivery={receipt.id}", outcome="reserved"
        )
        if native:
            await submit_reserved(receipt.id, ctx)
        elif mode == "queue" and lane.status == "idle":
            from .queue import drain_next_queued_message

            await drain_next_queued_message(ctx, lane.id)
        elif mode == "send":
            await submit_reserved(receipt.id, ctx)
    receipt = await ctx.registry.get_delivery(receipt.id)
    return DeliveryView.model_validate(receipt.model_dump(mode="json"))


async def submit_reserved(delivery_id: str, ctx: Ctx) -> bool:
    receipt = await ctx.registry.get_delivery(delivery_id)
    lane = await ctx.registry.find_lane(receipt.lane)
    if lane is None:
        raise NotFoundError(f"no managed thread {receipt.lane!r}")
    request = decode_prepared_request(receipt)
    if not await ctx.registry.claim_delivery(delivery_id):
        return False
    provider_call_entered = False
    try:
        expected_action = (
            ProviderAction.QUEUE_NATIVE
            if receipt.transport == "native_queue"
            else ProviderAction.SEND
        )
        if (
            request.target.lane_id != receipt.lane
            or request.action != expected_action
            or request.transport != receipt.transport
            or request.correlation_id != receipt.id
        ):
            raise ValidationError("reserved provider request does not match its delivery receipt")
        if (
            lane.provider,
            lane.binding_id,
            lane.provider_session_id,
        ) != (
            request.target.provider,
            request.target.binding_id,
            request.target.native_session_id,
        ):
            raise CapabilityUnavailableError(
                "reserved provider target no longer matches the current thread binding"
            )
        if receipt.transport == "native_queue":
            if lane.source != "attached" or not ctx.policy.allow_attached_writes:
                await ctx.registry.update_delivery(
                    delivery_id,
                    status="failed",
                    error="attached-write policy revoked before submission",
                )
                return True
        elif lane.source != "own":
            raise AuthorityError("reserved turn submission requires a Dispatch-owned thread")

        route = router_for(ctx).route_submission_target(request.target, request.action)
        route.recheck()
        if request.target.generation is not None:
            route.recheck_generation(request.target.generation)
        if receipt.transport == "turn":
            await ctx.registry.update_lane_status(receipt.lane, "busy")
        route.recheck()
        if request.target.generation is not None:
            route.recheck_generation(request.target.generation)
        provider_call_entered = True
        result = await route.adapter.submit_prepared(request)
        if isinstance(result, ProviderSubmissionRejected):
            await ctx.registry.update_delivery(
                delivery_id,
                status="failed",
                error=result.error[:2000],
            )
            if receipt.transport == "native_queue":
                provider_call_entered = False
                raise CapabilityUnavailableError(
                    f"native queue rejected by the connected Codex provider: {result.error}; "
                    f"receipt {receipt.id} is failed; no resume, start or steer fallback"
                )
            await ctx.registry.record_turn_request_failed(receipt.lane, result.error[:2000])
            return True
        if not isinstance(result, ProviderSubmissionAccepted):
            await ctx.registry.apply_receipt_observation(
                ProviderObservation(
                    provider=request.target.provider,
                    binding_id=request.target.binding_id,
                    native_session_id=request.target.native_session_id,
                    kind="uncertain",
                    correlation=ProviderCorrelation(
                        delivery_id=receipt.id, correlation_id=request.correlation_id
                    ),
                    generation=route.availability.generation,
                    source="submit_result",
                    received_at=datetime.fromisoformat(ctx.registry.now_iso()),
                    partial=True,
                    reason=result.error[:2000],
                )
            )
            return True
        await ctx.registry.apply_receipt_observation(
            ProviderObservation(
                provider=request.target.provider,
                binding_id=request.target.binding_id,
                native_session_id=request.target.native_session_id,
                kind="accepted",
                correlation=ProviderCorrelation(
                    delivery_id=receipt.id,
                    correlation_id=request.correlation_id,
                    native_submission_id=result.submission_id,
                    native_run_id=result.turn_id,
                ),
                generation=route.availability.generation,
                source="submit_result",
                received_at=datetime.fromisoformat(ctx.registry.now_iso()),
                partial=result.evidence_partial,
                reason=result.uncertainty_reason,
            )
        )
        if result.turn_id is not None:
            await observe_delivery_execution(receipt.lane, result.turn_id, ctx)
    except asyncio.CancelledError:
        await ctx.registry.update_delivery(
            delivery_id,
            status="ambiguous",
            error="submission cancelled; provider outcome unknown",
        )
        raise
    except DispatchError as exc:
        await ctx.registry.update_delivery(
            delivery_id,
            status="ambiguous" if provider_call_entered else "failed",
            error=str(exc)[:2000],
        )
        raise
    except Exception as exc:
        # A local bookkeeping failure must not strand a live daemon in submitting.
        # If the database itself remains unavailable, restart recovery retains the claim.
        try:
            await ctx.registry.update_delivery(
                delivery_id,
                status="ambiguous" if provider_call_entered else "failed",
                error=f"local submission bookkeeping failed: {exc}"[:2000],
            )
        except Exception:
            ctx.log.exception("delivery.bookkeeping_unavailable", delivery_id=delivery_id)
        raise
    return True


async def observe_delivery_execution(
    lane: str, turn_id: str | None, ctx: Ctx, *, generation: str | None = None
) -> None:
    """Join durable lifecycle facts after either side of the ACK/event race."""
    if turn_id is None:
        return
    managed = await ctx.registry.find_lane(lane)
    if managed is None or managed.provider_session_id is None:
        return
    try:
        turn = await ctx.registry.get_thread_turn(
            managed.provider,
            managed.provider_session_id,
            turn_id,
            binding_id=managed.binding_id,
        )
    except NotFoundError:
        return
    if turn.status not in ("completed", "failed", "interrupted"):
        return
    for receipt in await ctx.registry.delivery_for_provider_run(
        managed.provider, managed.binding_id, managed.provider_session_id, turn_id
    ):
        await ctx.registry.apply_receipt_observation(
            ProviderObservation(
                provider=managed.provider,
                binding_id=managed.binding_id,
                native_session_id=managed.provider_session_id,
                kind=turn.status,
                correlation=ProviderCorrelation(
                    delivery_id=receipt.id,
                    correlation_id=receipt.correlation_id,
                    native_submission_id=receipt.submission_id,
                    native_run_id=turn_id,
                ),
                generation=generation,
                source="live",
                received_at=datetime.fromisoformat(ctx.registry.now_iso()),
                reason=turn.error,
            )
        )
