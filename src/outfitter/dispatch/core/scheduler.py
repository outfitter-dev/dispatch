"""Time-trigger scheduler (ADR-0003): our own asyncio scheduler.

``tick(now)`` fires any due time triggers (interval/cron/idle_for) under their
guards — separated from the sleep loop so tests drive it with a fake clock.
Cron via ``croniter`` (NOT iCal RRULE). Event triggers are the reactor's job.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from croniter import croniter

from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.registry.models import (
    CronWhen,
    IdleForWhen,
    IntervalWhen,
    Trigger,
)

from .triggers import Clock, TriggerRunner, resolve_lane


class Scheduler:
    def __init__(self, ctx: Ctx, runner: TriggerRunner, now: Clock) -> None:
        self._ctx = ctx
        self._runner = runner
        self._now = now

    async def is_due(self, trigger: Trigger, now: datetime) -> bool:
        when = trigger.when
        last = trigger.last_fired_at
        if isinstance(when, IntervalWhen):
            return last is None or (now - last).total_seconds() >= when.seconds
        if isinstance(when, CronWhen):
            # Baseline = last fire, else the trigger's creation time (NOT `now`, which
            # would slide forward every tick and never come due).
            base = last if last is not None else (trigger.created_at or now)
            nxt: datetime = croniter(when.expr, base).get_next(datetime)
            return now >= nxt
        if isinstance(when, IdleForWhen):
            return await self._idle_due(trigger, when, now)
        return False  # EventWhen → reactor

    async def _idle_due(self, trigger: Trigger, when: IdleForWhen, now: datetime) -> bool:
        lane = await resolve_lane(self._ctx, trigger.lane)
        if lane is None or lane.status != "idle" or lane.last_event_at is None:
            return False
        if (now - lane.last_event_at).total_seconds() < when.seconds:
            return False
        # fire once per idle period: skip if we already fired since the last activity
        return trigger.last_fired_at is None or trigger.last_fired_at < lane.last_event_at

    async def tick(self, now: datetime | None = None) -> int:
        """Fire all due time triggers. Returns the number fired."""
        moment = now if now is not None else self._now()
        fired = 0
        for trigger in await self._ctx.registry.list_triggers():
            if not trigger.enabled or _is_event(trigger):
                continue
            if await self.is_due(trigger, moment) and await self._runner.maybe_fire(
                trigger, reason="time"
            ):
                fired += 1
        return fired

    async def run(self, poll_seconds: float = 1.0) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # never let one bad tick kill the scheduler
                self._ctx.log.exception("scheduler.tick_failed")
            await asyncio.sleep(poll_seconds)


def _is_event(trigger: Trigger) -> bool:
    return trigger.when.kind == "event"
