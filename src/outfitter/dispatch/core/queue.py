"""Durable per-lane send queue.

Queued sends are stored before delivery and drained one at a time when a lane is
idle. The queue intentionally starts only one turn per idle transition; the App
Server/reaction loop owns the next transition.
"""

from __future__ import annotations

from outfitter.dispatch.client.errors import ClientError
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import DispatchError, project_error
from outfitter.dispatch.registry.models import Lane, MessageReceipt, QueuedMessage

from .capture import bound_text
from .turn_settings import load_turn_start_settings


async def drain_next_queued_message(ctx: Ctx, lane_id: str) -> bool:
    """Start one pending queued message for an idle lane.

    Returns True when a message was claimed. A failed delivery marks the queued
    message as error and audits it, but does not raise into the reactor/scheduler.
    """
    lane = await ctx.registry.find_lane(lane_id)
    if lane is None or lane.status != "idle":
        return False
    message = await ctx.registry.next_pending_message(lane.id)
    if message is None:
        return False
    if not await ctx.registry.claim_queued_message(message.id):
        return False
    try:
        if lane.source == "attached" and ctx.policy.allow_attached_writes:
            await ctx.client.thread_resume(lane.id, exclude_turns=True)
        turn_settings = await load_turn_start_settings(ctx.registry, lane.id)
        await ctx.registry.update_lane_status(lane.id, "busy")
        await ctx.client.turn_start(
            lane.id,
            message.text,
            cwd=lane.cwd or ".",
            approval_policy=turn_settings.approval_policy,
            approvals_reviewer=turn_settings.approvals_reviewer,
            sandbox_policy=turn_settings.sandbox_policy,
            effort=turn_settings.effort,
            summary=turn_settings.summary,
            model=turn_settings.model,
            service_tier=turn_settings.service_tier,
            output_schema=turn_settings.output_schema,
            personality=turn_settings.personality,
        )
    except (DispatchError, ClientError) as exc:
        projected = project_error(exc)
        error = _bounded_error(exc, ctx)
        await ctx.registry.fail_queued_message(message.id, projected.code)
        await _record_queue_receipt(ctx, lane, message, status="failed", error=error)
        await ctx.registry.record_turn_request_failed(lane.id, error)
        await ctx.registry.log_action(
            "queue",
            lane=lane.id,
            detail=message.text[:120],
            outcome=projected.code,
        )
        return True
    await _record_queue_receipt(ctx, lane, message, status="sent")
    await ctx.registry.complete_queued_message(message.id)
    await ctx.registry.mark_inbox_delivered_for_queue(message.id, ack=True)
    await ctx.registry.log_action("send", lane=lane.id, detail=message.text[:120], outcome="queued")
    return True


def _bounded_error(exc: BaseException, ctx: Ctx) -> str:
    bounded = bound_text(str(exc), ctx.capture)
    return bounded.text if bounded is not None else ""


async def _record_queue_receipt(
    ctx: Ctx,
    lane: Lane,
    message: QueuedMessage,
    *,
    status: str,
    error: str | None = None,
) -> None:
    now = ctx.registry.now_iso()
    await ctx.registry.upsert_message_receipt(
        MessageReceipt(
            lane=lane.id,
            queued_message_id=message.id,
            provider="codex",
            provider_thread_id=lane.id,
            dispatch_message_id=f"queue:{message.id}",
            status=status,  # type: ignore[arg-type]
            error=error,
            created_at=message.created_at.isoformat(),
            sent_at=now if status == "sent" else None,
            failed_at=now if status == "failed" else None,
            updated_at=now,
        )
    )


async def drain_idle_queues(ctx: Ctx) -> int:
    """Drain one queued message for every currently idle lane."""
    drained = 0
    await ctx.registry.reset_sending_messages()
    for lane in await ctx.registry.list_lanes():
        if lane.status == "idle" and await drain_next_queued_message(ctx, lane.id):
            drained += 1
    return drained
