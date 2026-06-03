"""Op handlers. Surface-agnostic: input + ctx in, output or raise out. They never
import CLI/MCP/socket types; side effects go through ``ctx`` (ADR-0006).

Authority guard (ADR-0005): owned lanes are read/write; attached lanes are
observe-only — ``send``/``steer``/``brief``/``interrupt`` raise ``AuthorityError``.
"""

from __future__ import annotations

import asyncio

from outfitter.dispatch.client.models import SandboxPolicy, ThreadInfo
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    AuthorityError,
    NotFoundError,
    ValidationError,
)
from outfitter.dispatch.registry.models import Lane

from .models import (
    ActionAck,
    ActionView,
    AttachInput,
    DiscoveredSession,
    DiscoverInput,
    Discovery,
    LaneDetail,
    LaneInput,
    LaneRef,
    LaneTextInput,
    LogInput,
    LogOutput,
    OpenInput,
    Roster,
    RosterInput,
    StatusInput,
    StatusOutput,
)

_READ_ONLY = SandboxPolicy(type="readOnly")

# Bound ``thread/resume`` during attach: a persisted resume is a quick state-db read,
# so a stuck one means the app-server is wedged. Fail with a clear error rather than
# hang — and never half-register (the registry write only follows a successful resume).
_RESUME_TIMEOUT_S = 15.0

_PREVIEW_MAX = 80


def _ref(lane: Lane) -> LaneRef:
    return LaneRef(id=lane.id, handle=lane.handle, source=lane.source, status=lane.status)


async def _resolve(ctx: Ctx, ref: str) -> Lane:
    lane = await ctx.registry.find_lane(ref)
    if lane is None:
        lane = await ctx.registry.find_lane_by_handle(ref)
    if lane is None:
        raise NotFoundError(f"no lane {ref!r}")
    return lane


def _require_writable(lane: Lane) -> None:
    if lane.source == "attached":
        raise AuthorityError(f"lane {lane.handle} is attached (observe-only; ADR-0005)")


async def open_lane(inp: OpenInput, ctx: Ctx) -> LaneRef:
    thread = await ctx.client.thread_start(cwd=inp.cwd, sandbox="read-only", ephemeral=False)
    handle = inp.name if inp.name.startswith("@") else f"@{inp.name}"
    lane = await ctx.registry.add_lane(
        id=thread.id, handle=handle, source="own", cwd=inp.cwd, status="idle"
    )
    await ctx.registry.log_action("open", lane=lane.id, detail=handle)
    ctx.log.info("lane.open", lane=lane.id, handle=handle)
    return _ref(lane)


async def attach_lane(inp: AttachInput, ctx: Ctx) -> LaneRef:
    existing = await ctx.registry.find_lane(inp.thread)
    if existing is not None:
        return _ref(existing)  # idempotent re-attach
    try:
        thread = await asyncio.wait_for(ctx.client.thread_resume(inp.thread), _RESUME_TIMEOUT_S)
    except TimeoutError as exc:
        # The registry write below never ran, so no lane is half-registered.
        raise AppServerError(
            f"attach timed out: thread/resume for {inp.thread!r} exceeded "
            f"{_RESUME_TIMEOUT_S:.0f}s (no lane registered)"
        ) from exc
    handle = thread.name or f"@{inp.thread[:8]}"
    lane = await ctx.registry.add_lane(
        id=thread.id, handle=handle, source="attached", cwd=thread.cwd, status="idle"
    )
    await ctx.registry.log_action("attach", lane=lane.id, detail=handle)
    ctx.log.info("lane.attach", lane=lane.id, handle=handle)
    return _ref(lane)


async def send(inp: LaneTextInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)
    await ctx.client.turn_start(lane.id, inp.text, cwd=lane.cwd or ".", sandbox_policy=_READ_ONLY)
    await ctx.registry.log_action("send", lane=lane.id, detail=inp.text[:120])
    return ActionAck(lane=lane.id, op="send")


async def steer(inp: LaneTextInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)
    if lane.active_turn_id is None:
        raise ValidationError(f"lane {lane.handle} has no active turn to steer")
    await ctx.client.turn_steer(lane.id, lane.active_turn_id, inp.text)
    await ctx.registry.log_action("steer", lane=lane.id, detail=inp.text[:120])
    return ActionAck(lane=lane.id, op="steer")


async def brief(inp: LaneTextInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)
    item: dict[str, object] = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": inp.text}],
    }
    await ctx.client.inject_items(lane.id, [item])
    await ctx.registry.log_action("brief", lane=lane.id, detail=inp.text[:120])
    return ActionAck(lane=lane.id, op="brief")


async def interrupt(inp: LaneInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)
    await ctx.client.turn_interrupt(lane.id, lane.active_turn_id)
    await ctx.registry.log_action("interrupt", lane=lane.id)
    return ActionAck(lane=lane.id, op="interrupt")


async def show(inp: LaneInput, ctx: Ctx) -> LaneDetail:
    lane = await _resolve(ctx, inp.lane)
    return LaneDetail(
        id=lane.id,
        handle=lane.handle,
        source=lane.source,
        status=lane.status,
        cwd=lane.cwd,
        active_turn_id=lane.active_turn_id,
    )


async def roster(inp: RosterInput, ctx: Ctx) -> Roster:
    lanes = await ctx.registry.list_lanes(include_archived=inp.include_archived)
    return Roster(lanes=[_ref(lane) for lane in lanes])


def _short(text: str | None, limit: int = _PREVIEW_MAX) -> str | None:
    if text is None:
        return None
    collapsed = " ".join(text.split())  # flatten whitespace/newlines for a one-line preview
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _session(thread: ThreadInfo) -> DiscoveredSession:
    return DiscoveredSession(
        id=thread.id,
        name=thread.name,
        preview=_short(thread.preview),
        cwd=thread.cwd,
        status=thread.status.type if thread.status is not None else None,
        source=thread.source,
        ephemeral=thread.ephemeral,
    )


async def discover(inp: DiscoverInput, ctx: Ctx) -> Discovery:
    """List persisted Codex sessions (``thread/list``, state-db only) — read-only and
    distinct from ``roster``: these are candidates to ``attach``, not managed lanes.
    Discovery does not resume or register anything (ADR-0005 observe-only is untouched)."""
    threads = await ctx.client.thread_list(limit=inp.limit, use_state_db_only=True)
    return Discovery(sessions=[_session(thread) for thread in threads])


async def archive(inp: LaneInput, ctx: Ctx) -> LaneRef:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)  # archiving mutates the shared thread store (ADR-0005)
    await ctx.client.thread_archive(lane.id)
    await ctx.registry.update_lane_status(lane.id, "archived")
    await ctx.registry.log_action("archive", lane=lane.id)
    return _ref(await ctx.registry.get_lane(lane.id))


async def status(inp: StatusInput, ctx: Ctx) -> StatusOutput:
    lanes = await ctx.registry.list_lanes()
    triggers = await ctx.registry.list_triggers()
    return StatusOutput(
        lanes=len(lanes),
        idle=sum(1 for lane in lanes if lane.status == "idle"),
        busy=sum(1 for lane in lanes if lane.status == "busy"),
        triggers=len(triggers),
        triggers_enabled=sum(1 for trigger in triggers if trigger.enabled),
    )


async def show_log(inp: LogInput, ctx: Ctx) -> LogOutput:
    records = await ctx.registry.recent_actions(inp.limit)
    return LogOutput(
        actions=[
            ActionView(
                ts=record.ts.isoformat(),
                op=record.op,
                lane=record.lane,
                trigger_id=record.trigger_id,
                outcome=record.outcome,
                detail=record.detail,
            )
            for record in records
        ]
    )
