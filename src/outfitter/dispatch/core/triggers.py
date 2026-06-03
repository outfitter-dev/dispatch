"""Trigger firing + guards (ADR-0003). The runner evaluates guards, performs the
action through the existing op handlers (so authority/idle rules are reused), and
records every firing in ``actions_log`` with the trigger id.

The guard layer (``idle_only``, ``min_interval``, ``dedupe``) is also the seam for
future conditional triggers (interface only in v0).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from outfitter.dispatch.client.errors import ClientError
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import DispatchError, project_error
from outfitter.dispatch.registry.models import (
    Action,
    BriefAction,
    Lane,
    SendAction,
    SteerAction,
    Trigger,
)

from . import handlers
from .models import LaneTextInput

Clock = Callable[[], datetime]


def action_text(action: Action) -> str:
    return action.text


def action_op(action: Action) -> str:
    return action.kind


async def resolve_lane(ctx: Ctx, selector: str) -> Lane | None:
    """Resolve a lane selector (id or @handle) to a lane, or None."""
    lane = await ctx.registry.find_lane(selector)
    if lane is None:
        lane = await ctx.registry.find_lane_by_handle(selector)
    return lane


class TriggerRunner:
    """Fires triggers under their guards and audits every attempt."""

    def __init__(self, ctx: Ctx, now: Clock) -> None:
        self._ctx = ctx
        self._now = now
        # trigger id -> last fired text (dedupe). Process-local: resets on daemon
        # restart, so the first post-restart firing always passes the dedupe guard.
        self._last_text: dict[str, str] = {}

    async def maybe_fire(self, trigger: Trigger, *, reason: str) -> bool:
        """Fire the trigger if its guards allow. Returns whether it fired."""
        if not trigger.enabled:
            return False
        now = self._now()
        guard = trigger.guard
        if (
            guard.min_interval is not None
            and trigger.last_fired_at is not None
            and (now - trigger.last_fired_at).total_seconds() < guard.min_interval
        ):
            return False
        lane = await resolve_lane(self._ctx, trigger.lane)
        if guard.idle_only and lane is not None and lane.status != "idle":
            return False
        text = action_text(trigger.action)
        if guard.dedupe and self._last_text.get(trigger.id) == text:
            return False

        outcome = "ok"
        try:
            await self._run_action(trigger)
        except (DispatchError, ClientError) as exc:
            # A failed firing must not kill the scheduler/reactor — audit and move on.
            outcome = project_error(exc).code
        await self._ctx.registry.set_trigger_fired(trigger.id, now)
        await self._ctx.registry.log_action(
            action_op(trigger.action),
            lane=trigger.lane,
            trigger_id=trigger.id,
            detail=f"{reason}:{text[:80]}",
            outcome=outcome,
        )
        self._last_text[trigger.id] = text
        return True

    async def _run_action(self, trigger: Trigger) -> None:
        action = trigger.action
        inp = LaneTextInput(lane=trigger.lane, text=action.text)
        if isinstance(action, SendAction):
            await handlers.send(inp, self._ctx)
        elif isinstance(action, SteerAction):
            await handlers.steer(inp, self._ctx)
        elif isinstance(action, BriefAction):
            await handlers.brief(inp, self._ctx)
