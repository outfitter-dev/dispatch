"""Trigger management handlers (add/list/rm/pause/resume).

The flat CLI/MCP input is translated here into the structured ``Trigger`` (the
boundary translation), then persisted. Surface-agnostic like all handlers.
"""

from __future__ import annotations

import uuid

from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import NotFoundError, ValidationError
from outfitter.dispatch.registry.models import (
    Action,
    BriefAction,
    CronWhen,
    EventWhen,
    Guard,
    IdleForWhen,
    IntervalWhen,
    SendAction,
    SteerAction,
    Trigger,
    When,
)

from .models import (
    TriggerAddInput,
    TriggerIdInput,
    TriggerList,
    TriggerListInput,
    TriggerRemoved,
    TriggerView,
)


def _when_label(trigger: Trigger) -> str:
    return trigger.when.event if isinstance(trigger.when, EventWhen) else trigger.when.kind


def _view(trigger: Trigger) -> TriggerView:
    return TriggerView(
        id=trigger.id,
        name=trigger.name,
        lane=trigger.lane,
        when=_when_label(trigger),
        action=trigger.action.kind,
        enabled=trigger.enabled,
    )


def _build_when(inp: TriggerAddInput) -> When:
    match inp.when:
        case "interval":
            if inp.seconds is None:
                raise ValidationError("interval trigger requires --seconds")
            return IntervalWhen(seconds=inp.seconds)
        case "cron":
            if inp.cron is None:
                raise ValidationError("cron trigger requires --cron")
            return CronWhen(expr=inp.cron)
        case "idle_for":
            if inp.seconds is None:
                raise ValidationError("idle_for trigger requires --seconds")
            return IdleForWhen(seconds=inp.seconds)
        case "turn_completed" | "waiting_on_approval":
            return EventWhen(event=inp.when)


def _build_action(inp: TriggerAddInput) -> Action:
    match inp.action:
        case "send":
            return SendAction(text=inp.text)
        case "steer":
            return SteerAction(text=inp.text)
        case "brief":
            return BriefAction(text=inp.text)


async def trigger_add(inp: TriggerAddInput, ctx: Ctx) -> TriggerView:
    trigger = Trigger(
        id=uuid.uuid4().hex[:8],
        name=inp.name,
        lane=inp.lane,
        when=_build_when(inp),
        action=_build_action(inp),
        guard=Guard(idle_only=inp.idle_only, min_interval=inp.min_interval, dedupe=inp.dedupe),
    )
    await ctx.registry.add_trigger(trigger)
    await ctx.registry.log_action(
        "trigger-add", lane=inp.lane, trigger_id=trigger.id, detail=inp.name
    )
    return _view(trigger)


async def trigger_list(inp: TriggerListInput, ctx: Ctx) -> TriggerList:
    return TriggerList(triggers=[_view(t) for t in await ctx.registry.list_triggers()])


async def trigger_rm(inp: TriggerIdInput, ctx: Ctx) -> TriggerRemoved:
    if not await ctx.registry.remove_trigger(inp.id):
        raise NotFoundError(f"no trigger {inp.id!r}")
    await ctx.registry.log_action("trigger-rm", trigger_id=inp.id)
    return TriggerRemoved(id=inp.id, removed=True)


async def trigger_pause(inp: TriggerIdInput, ctx: Ctx) -> TriggerView:
    trigger = await ctx.registry.get_trigger(inp.id)
    await ctx.registry.set_trigger_enabled(inp.id, False)
    return _view(trigger.model_copy(update={"enabled": False}))


async def trigger_resume(inp: TriggerIdInput, ctx: Ctx) -> TriggerView:
    trigger = await ctx.registry.get_trigger(inp.id)
    await ctx.registry.set_trigger_enabled(inp.id, True)
    return _view(trigger.model_copy(update={"enabled": True}))
