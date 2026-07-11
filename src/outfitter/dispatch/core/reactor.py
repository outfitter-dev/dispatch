"""React to normalized lane events and generic interactive server requests."""

from __future__ import annotations

import asyncio

from outfitter.dispatch.client.events import (
    AccountRateLimitsUpdated,
    ApprovalRequested,
    GoalCleared,
    GoalUpdated,
    ItemCompleted,
    ItemStarted,
    LaneEvent,
    LaneIdle,
    ThreadArchived,
    ThreadCompacted,
    ThreadDeleted,
    ThreadStarted,
    ThreadUnarchived,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.registry.models import EventWhen

from .capacity import observe_codex_rate_limits
from .capture import bound_text
from .event_index import index_codex_lane_event
from .queue import drain_next_queued_message
from .server_requests import ServerRequestManager
from .subscriptions import process_event_subscriptions
from .topology import observe_thread
from .triggers import TriggerRunner, resolve_lane


class Reactor:
    def __init__(self, ctx: Ctx, runner: TriggerRunner) -> None:
        self._ctx = ctx
        self._runner = runner

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._run_lane_events())
            tasks.create_task(self._run_account_events())
            tasks.create_task(
                ServerRequestManager(
                    self._ctx,
                    on_approval_attention=lambda lane_id: self._fire_event(
                        lane_id, "waiting_on_approval"
                    ),
                ).run()
            )

    async def _run_lane_events(self) -> None:
        async for event in self._ctx.client.events(None):
            try:
                await self.handle(event)
            except Exception:  # never let one bad event kill the reactor
                self._ctx.log.exception("reactor.handle_failed", lane=event.lane_id)

    async def _run_account_events(self) -> None:
        async for event in self._ctx.client.account_events():
            try:
                await self.handle_account_event(event)
            except Exception:
                self._ctx.log.exception("reactor.account_event_failed")

    async def handle_account_event(self, event: AccountRateLimitsUpdated) -> None:
        await observe_codex_rate_limits(self._ctx, event.rate_limits)

    async def handle(self, event: LaneEvent) -> None:
        registry = self._ctx.registry
        if isinstance(event, ThreadStarted) and event.thread is not None:
            await observe_thread(
                registry,
                event.thread,
                lifecycle_state="active",
                relationship_source="thread/started",
            )
        elif isinstance(event, ThreadArchived):
            await registry.mark_provider_thread_state("codex", event.lane_id, "archived")
        elif isinstance(event, ThreadUnarchived):
            await registry.mark_provider_thread_state("codex", event.lane_id, "active")
        elif isinstance(event, ThreadDeleted):
            await registry.mark_provider_thread_state("codex", event.lane_id, "deleted")
        lane = await registry.find_lane(event.lane_id)
        if lane is None:
            return  # an event for a thread dispatch does not track
        await index_codex_lane_event(registry, lane, event, self._ctx.capture)

        if isinstance(event, TurnStarted):
            await registry.record_turn_started(lane.id, event.turn_id)
            await registry.touch_lane_event(lane.id)
        elif isinstance(event, TurnCompleted):
            await registry.record_turn_completed(lane.id, event.turn_id)
            await registry.touch_lane_event(lane.id)
            await process_event_subscriptions(self._ctx, lane, event)
            await self._fire_event(lane.id, "turn_completed")
            await drain_next_queued_message(self._ctx, lane.id)
        elif isinstance(event, TurnFailed):
            message = bound_text(event.message, self._ctx.capture)
            await registry.record_turn_failed(
                lane.id,
                event.turn_id,
                message.text if message is not None else None,
            )
            await registry.touch_lane_event(lane.id)
            await process_event_subscriptions(self._ctx, lane, event)
        elif isinstance(event, LaneIdle):
            await registry.mark_lane_idle(lane.id)
            await registry.touch_lane_event(lane.id)
            await process_event_subscriptions(self._ctx, lane, event)
            await drain_next_queued_message(self._ctx, lane.id)
        elif isinstance(event, ThreadArchived | ThreadDeleted):
            await registry.update_lane_status(lane.id, "archived")
            await registry.touch_lane_event(lane.id)
        elif isinstance(event, ThreadUnarchived):
            await registry.mark_lane_idle(lane.id)
            await registry.touch_lane_event(lane.id)
        elif isinstance(
            event,
            ItemStarted
            | ItemCompleted
            | GoalUpdated
            | GoalCleared
            | ThreadCompacted
            | ThreadStarted
            | ApprovalRequested,
        ):
            await registry.touch_lane_event(lane.id)

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
