"""Reserved text deliveries; provider acceptance is not execution completion."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from pydantic import TypeAdapter

from outfitter.dispatch.client.errors import AppServerError, ClientError
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import AuthorityError, NotFoundError, ValidationError
from outfitter.dispatch.registry.models import Lane

from .models import DeliveryLookupInput, DeliveryView, SendInput
from .turn_settings import TurnStartSettings, load_turn_start_settings

_SETTINGS = TypeAdapter(TurnStartSettings)


async def get_receipt(inp: DeliveryLookupInput, ctx: Ctx) -> DeliveryView:
    receipt = await ctx.registry.get_delivery(inp.receipt_id)
    return DeliveryView.model_validate(receipt.model_dump(mode="json"))


async def reconcile_receipt_request(inp: DeliveryLookupInput, ctx: Ctx) -> DeliveryView:
    from .delivery_reconciliation import reconcile_receipt

    await reconcile_receipt(inp.receipt_id, ctx, automatic=False)
    return await get_receipt(inp, ctx)


async def send_reserved(inp: SendInput, lane: Lane, text: str, ctx: Ctx) -> DeliveryView:
    if lane.source != "own":
        raise AuthorityError("idempotent delivery currently requires a Dispatch-owned thread")
    mode = inp.mode
    if mode != "send" and mode != "queue":
        raise ValidationError("idempotent delivery supports send or queue")
    settings = await load_turn_start_settings(ctx.registry, lane.id)
    payload = json.dumps(
        {
            "text": text,
            "cwd": lane.cwd or ".",
            "settings": _SETTINGS.dump_python(settings, mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    delivery_id = str(uuid4())
    receipt, created = await ctx.registry.reserve_delivery(
        key=inp.idempotency_key,
        lane=lane.id,
        mode=mode,
        payload=payload,
        text=text,
        delivery_id=delivery_id,
    )
    if created:
        await ctx.registry.log_action(
            mode, lane=lane.id, detail=f"delivery={receipt.id}", outcome="reserved"
        )
        if mode == "queue" and lane.status == "idle":
            from .queue import drain_next_queued_message

            await drain_next_queued_message(ctx, lane.id)
        elif mode == "send":
            await submit_reserved(receipt.id, ctx)
    receipt = await ctx.registry.get_delivery(receipt.id)
    return DeliveryView.model_validate(receipt.model_dump(mode="json"))


async def submit_reserved(delivery_id: str, ctx: Ctx) -> bool:
    receipt = await ctx.registry.get_delivery(delivery_id)
    payload = json.loads(receipt.payload)
    settings = _SETTINGS.validate_python(payload["settings"])
    if not await ctx.registry.claim_delivery(delivery_id):
        return False
    provider_call_entered = False
    try:
        await ctx.registry.update_lane_status(receipt.lane, "busy")
        async with asyncio.timeout(15):
            provider_call_entered = True
            result = await ctx.client.turn_start(
                receipt.lane,
                payload["text"],
                cwd=payload["cwd"],
                client_user_message_id=receipt.id,
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
        await ctx.registry.update_delivery(
            delivery_id,
            status="accepted",
            turn_id=turn_id if isinstance(turn_id, str) else None,
        )
        if isinstance(turn_id, str):
            await observe_delivery_execution(receipt.lane, turn_id, ctx)
    except (ClientError, TimeoutError) as exc:
        # Only protocol-level request rejection proves this attempt never ran.
        definite = isinstance(exc, AppServerError) and exc.code in {-32600, -32601, -32602}
        await ctx.registry.update_delivery(
            delivery_id,
            status="failed" if definite else "ambiguous",
            error=str(exc)[:2000],
        )
        if definite:
            await ctx.registry.record_turn_request_failed(receipt.lane, str(exc)[:2000])
        return True
    except asyncio.CancelledError:
        await ctx.registry.update_delivery(
            delivery_id,
            status="ambiguous",
            error="submission cancelled; provider outcome unknown",
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


async def observe_delivery_execution(lane: str, turn_id: str | None, ctx: Ctx) -> None:
    """Join durable lifecycle facts after either side of the ACK/event race."""
    if turn_id is None:
        return
    try:
        turn = await ctx.registry.get_thread_turn("codex", lane, turn_id)
    except NotFoundError:
        return
    if turn.status not in ("completed", "failed", "interrupted"):
        return
    for receipt in await ctx.registry.delivery_for_turn(lane, turn_id):
        await ctx.registry.update_delivery(
            receipt.id,
            status="completed" if turn.status == "completed" else "accepted",
            execution_status=turn.status,
            error=turn.error,
        )
