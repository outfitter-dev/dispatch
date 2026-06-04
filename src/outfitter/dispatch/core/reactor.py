"""Event reactor (ADR-0003 + ADR-0007): consume normalized LaneEvents to keep
lane state current (so steer/interrupt and idle_only guards work) and to fire
event triggers. Unhandled ``waiting_on_approval`` defaults to ``decline``.
"""

from __future__ import annotations

from outfitter.dispatch.client.events import (
    ApprovalRequested,
    GoalCleared,
    GoalUpdated,
    ItemCompleted,
    LaneEvent,
    LaneIdle,
    ThreadCompacted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.registry.models import EventWhen

from .queue import drain_next_queued_message
from .triggers import TriggerRunner, resolve_lane


class Reactor:
    def __init__(self, ctx: Ctx, runner: TriggerRunner) -> None:
        self._ctx = ctx
        self._runner = runner

    async def run(self) -> None:
        async for event in self._ctx.client.events(None):
            try:
                await self.handle(event)
            except Exception:  # never let one bad event kill the reactor
                self._ctx.log.exception("reactor.handle_failed", lane=event.lane_id)

    async def handle(self, event: LaneEvent) -> None:
        registry = self._ctx.registry
        lane = await registry.find_lane(event.lane_id)
        if lane is None:
            return  # an event for a thread dispatch does not track

        if isinstance(event, TurnStarted):
            await registry.set_active_turn(lane.id, event.turn_id)
            await registry.update_lane_status(lane.id, "busy")
            await registry.touch_lane_event(lane.id)
        elif isinstance(event, TurnCompleted):
            await registry.set_active_turn(lane.id, None)
            await registry.update_lane_status(lane.id, "idle")
            await registry.touch_lane_event(lane.id)
            await self._fire_event(lane.id, "turn_completed")
            await drain_next_queued_message(self._ctx, lane.id)
        elif isinstance(event, TurnFailed):
            await registry.set_active_turn(lane.id, None)
            await registry.update_lane_status(lane.id, "error")
            await registry.touch_lane_event(lane.id)
        elif isinstance(event, LaneIdle):
            await registry.set_active_turn(lane.id, None)
            await registry.update_lane_status(lane.id, "idle")
            await registry.touch_lane_event(lane.id)
            await drain_next_queued_message(self._ctx, lane.id)
        elif isinstance(event, ItemCompleted | GoalUpdated | GoalCleared | ThreadCompacted):
            await registry.touch_lane_event(lane.id)
        elif isinstance(event, ApprovalRequested):
            await registry.update_lane_status(lane.id, "waiting_approval")
            await registry.touch_lane_event(lane.id)
            # Auto-decline only when NO handler is registered — a registered trigger
            # suppressed by its own guard still counts as "handled" (don't override intent).
            matched = await self._fire_event(lane.id, "waiting_on_approval")
            if matched == 0:
                await self._ctx.client.respond_approval(event.request_id, "decline")
                await registry.log_action(
                    "approval", lane=lane.id, detail="auto-decline (no trigger)", outcome="ok"
                )

    async def _fire_event(self, lane_id: str, event_name: str) -> int:
        """Fire matching enabled event triggers; return how many MATCHED (were
        registered for this event+lane), regardless of whether a guard suppressed
        the firing."""
        matched = 0
        for trigger in await self._ctx.registry.list_triggers():
            if not trigger.enabled:
                continue
            when = trigger.when
            if not isinstance(when, EventWhen) or when.event != event_name:
                continue
            target = await resolve_lane(self._ctx, trigger.lane)
            if target is not None and target.id == lane_id:
                matched += 1
                await self._runner.maybe_fire(trigger, reason="event")
        return matched
