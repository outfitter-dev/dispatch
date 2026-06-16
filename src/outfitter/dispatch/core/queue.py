"""Durable per-lane send queue.

Queued sends are stored before delivery and drained one at a time when a lane is
idle. The queue intentionally starts only one turn per idle transition; the App
Server/reaction loop owns the next transition.
"""

from __future__ import annotations

from outfitter.dispatch.client.errors import ClientError
from outfitter.dispatch.client.models import SandboxPolicy
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import DispatchError, project_error

_READ_ONLY = SandboxPolicy(type="readOnly")


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
        await ctx.client.turn_start(
            lane.id, message.text, cwd=lane.cwd or ".", sandbox_policy=_READ_ONLY
        )
    except (DispatchError, ClientError) as exc:
        projected = project_error(exc)
        await ctx.registry.fail_queued_message(message.id, projected.code)
        await ctx.registry.log_action(
            "queue",
            lane=lane.id,
            detail=message.text[:120],
            outcome=projected.code,
        )
        return True
    await ctx.registry.complete_queued_message(message.id)
    await ctx.registry.update_lane_status(lane.id, "busy")
    await ctx.registry.log_action("send", lane=lane.id, detail=message.text[:120], outcome="queued")
    return True


async def drain_idle_queues(ctx: Ctx) -> int:
    """Drain one queued message for every currently idle lane."""
    drained = 0
    await ctx.registry.reset_sending_messages()
    for lane in await ctx.registry.list_lanes():
        if lane.status == "idle" and await drain_next_queued_message(ctx, lane.id):
            drained += 1
    return drained
