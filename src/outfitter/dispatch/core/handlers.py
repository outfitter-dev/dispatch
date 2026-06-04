"""Op handlers. Surface-agnostic: input + ctx in, output or raise out. They never
import CLI/MCP/socket types; side effects go through ``ctx`` (ADR-0006).

Authority guard (ADR-0005): owned lanes are read/write; attached lanes are
observe-only — ``send``/``steer``/``brief``/``interrupt`` raise ``AuthorityError``.
"""

from __future__ import annotations

import asyncio

from outfitter.dispatch.client.errors import ClientError
from outfitter.dispatch.client.models import SandboxPolicy, ThreadGoal, ThreadInfo, ThreadSandbox
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    AuthorityError,
    DispatchError,
    NotFoundError,
    ValidationError,
    project_error,
)
from outfitter.dispatch.registry.models import Lane

from .models import (
    ActionAck,
    ActionView,
    AttachInput,
    CompactInput,
    DiscoveredSession,
    DiscoverInput,
    Discovery,
    ForkInput,
    Goal,
    GoalClearInput,
    GoalGetInput,
    GoalSetInput,
    GoalView,
    LaneDetail,
    LaneInput,
    LaneRef,
    LaneTextInput,
    LogInput,
    LogOutput,
    NewInput,
    NewLane,
    OpenInput,
    RollbackInput,
    Roster,
    RosterInput,
    ShowInput,
    StatusInput,
    StatusOutput,
    TranscriptInput,
    TranscriptItem,
    TranscriptOutput,
    WatchEvent,
    WatchInput,
    WatchOutput,
)
from .new_config import NewSettings, resolve_new

_READ_ONLY = SandboxPolicy(type="readOnly")

# Bound ``thread/resume`` during attach: a persisted resume is a quick state-db read,
# so a stuck one means the app-server is wedged. Fail with a clear error rather than
# hang — and never half-register (the registry write only follows a successful resume).
_RESUME_TIMEOUT_S = 15.0

_PREVIEW_MAX = 80


def _ref(lane: Lane) -> LaneRef:
    return LaneRef(id=lane.id, handle=lane.handle, source=lane.source, status=lane.status)


def _handle(name: str) -> str:
    return name if name.startswith("@") else f"@{name}"


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
    handle = _handle(inp.name)
    lane = await ctx.registry.add_lane(
        id=thread.id, handle=handle, source="own", cwd=inp.cwd, status="idle"
    )
    await ctx.registry.log_action("open", lane=lane.id, detail=handle)
    ctx.log.info("lane.open", lane=lane.id, handle=handle)
    return _ref(lane)


async def new_lane(inp: NewInput, ctx: Ctx) -> NewLane:
    resolved = resolve_new(
        name=inp.name,
        presets=inp.preset,
        cli=NewSettings(
            cwd=inp.cwd,
            sandbox=inp.sandbox,
            approval_policy=inp.approval_policy,
            approvals_reviewer=inp.approvals_reviewer,
            model=inp.model,
            model_provider=inp.model_provider,
            effort=inp.effort,
            summary=inp.summary,
            personality=inp.personality,
            service_tier=inp.service_tier,
            ephemeral=inp.ephemeral,
            prefix=inp.prefix,
            text=inp.text,
            base_instructions=inp.base_instructions,
            base_file=inp.base_file,
            developer_instructions=inp.developer_instructions,
            developer_file=inp.developer_file,
        ),
    )
    settings = resolved.settings
    sandbox = settings.sandbox or "read-only"
    approval_policy = settings.approval_policy or "never"
    thread = await ctx.client.thread_start(
        cwd=str(resolved.cwd),
        sandbox=sandbox,
        approval_policy=approval_policy,
        approvals_reviewer=settings.approvals_reviewer,
        base_instructions=resolved.base_instructions,
        developer_instructions=resolved.developer_instructions,
        personality=settings.personality,
        service_tier=settings.service_tier,
        model=settings.model,
        model_provider=settings.model_provider,
        ephemeral=bool(settings.ephemeral),
    )
    lane = await ctx.registry.add_lane(
        id=thread.id, handle=resolved.handle, source="own", cwd=str(resolved.cwd), status="idle"
    )
    await ctx.registry.log_action("new", lane=lane.id, detail=resolved.display_name)
    try:
        await ctx.client.thread_set_name(thread.id, resolved.display_name)
    except ClientError as exc:
        ctx.log.warning("lane.name_set_failed", lane=lane.id, error=str(exc))

    sent = False
    if settings.text is not None and inp.send:
        try:
            await ctx.client.turn_start(
                lane.id,
                settings.text,
                cwd=str(resolved.cwd),
                approval_policy=approval_policy,
                approvals_reviewer=settings.approvals_reviewer,
                sandbox_policy=_turn_sandbox(sandbox),
                effort=settings.effort,
                summary=settings.summary,
                model=settings.model,
                output_schema=settings.output_schema,
                personality=settings.personality,
            )
        except (DispatchError, ClientError) as exc:
            await ctx.registry.log_action(
                "send",
                lane=lane.id,
                detail=settings.text[:120],
                outcome=project_error(exc).code,
            )
            raise
        await ctx.registry.log_action("send", lane=lane.id, detail=settings.text[:120])
        sent = True
    ctx.log.info("lane.new", lane=lane.id, handle=lane.handle, sent=sent)
    ref = _ref(lane)
    return NewLane(**ref.model_dump(), sent=sent)


def _turn_sandbox(sandbox: ThreadSandbox) -> SandboxPolicy:
    match sandbox:
        case "read-only":
            return SandboxPolicy(type="readOnly")
        case "workspace-write":
            return SandboxPolicy(type="workspaceWrite")
        case "danger-full-access":
            return SandboxPolicy(type="dangerFullAccess")


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


async def show(inp: ShowInput, ctx: Ctx) -> LaneDetail:
    lane = await _resolve(ctx, inp.lane)
    transcript: list[TranscriptItem] = []
    if inp.include_transcript:
        result = await ctx.client.thread_read(lane.id, include_turns=True)
        transcript = _transcript_from_thread(result, limit=inp.max_items)
    return LaneDetail(
        id=lane.id,
        handle=lane.handle,
        source=lane.source,
        status=lane.status,
        cwd=lane.cwd,
        active_turn_id=lane.active_turn_id,
        transcript=transcript,
    )


async def watch(inp: WatchInput, ctx: Ctx) -> WatchOutput:
    lane = await _resolve(ctx, inp.lane)
    if inp.timeout == 0:
        return WatchOutput(lane=lane.id, events=[], timed_out=True)
    stream = ctx.client.raw_events(lane.id)
    events: list[WatchEvent] = []
    timed_out = False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + inp.timeout
    try:
        while len(events) < inp.limit:
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                raw = await asyncio.wait_for(anext(stream), timeout=remaining)
            except TimeoutError:
                timed_out = True
                break
            except StopAsyncIteration:
                break
            event = _watch_event(raw)
            if event is not None:
                events.append(event)
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()
    return WatchOutput(lane=lane.id, events=events, timed_out=timed_out)


async def transcript(inp: TranscriptInput, ctx: Ctx) -> TranscriptOutput:
    lane = await _resolve(ctx, inp.lane)
    result = await ctx.client.thread_read(lane.id, include_turns=True)
    return TranscriptOutput(
        lane=lane.id,
        items=_transcript_from_thread(result, limit=inp.limit),
    )


def _watch_event(raw: dict[str, object]) -> WatchEvent | None:
    method = raw.get("method")
    if not isinstance(method, str):
        return None
    params = raw.get("params")
    request_id = raw.get("id")
    return WatchEvent(
        method=method,
        params=params if isinstance(params, dict) else {},
        request_id=request_id if isinstance(request_id, int) else None,
    )


def _transcript_from_thread(result: dict[str, object], *, limit: int) -> list[TranscriptItem]:
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return []
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return []
    items: list[TranscriptItem] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        turn_id = _str(turn, "id")
        raw_items = turn.get("items")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            item_type = _str(item, "type") or "unknown"
            text = _item_text(item)
            items.append(
                TranscriptItem(
                    turn_id=turn_id,
                    item_id=_str(item, "id"),
                    type=item_type,
                    text=text,
                )
            )
    return items[-limit:]


def _item_text(item: dict[str, object]) -> str | None:
    direct = item.get("text")
    if isinstance(direct, str):
        return direct
    content = item.get("content")
    if isinstance(content, list):
        chunks: list[str] = []
        for entry in content:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text")
            if isinstance(text, str):
                chunks.append(text)
                continue
            nested = entry.get("content")
            if isinstance(nested, list):
                chunks.extend(_content_text(nested))
        return "\n".join(chunks) if chunks else None
    return None


def _content_text(entries: list[object]) -> list[str]:
    chunks: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            text = entry.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return chunks


def _str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _goal(goal: ThreadGoal) -> Goal:
    return Goal(
        thread_id=goal.thread_id,
        objective=goal.objective,
        status=goal.status,
        tokens_used=goal.tokens_used,
        time_used_seconds=goal.time_used_seconds,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        token_budget=goal.token_budget,
    )


async def goal_get(inp: GoalGetInput, ctx: Ctx) -> GoalView:
    lane = await _resolve(ctx, inp.lane)
    goal = await ctx.client.thread_goal_get(lane.id)
    return GoalView(lane=lane.id, goal=_goal(goal) if goal is not None else None)


async def goal_set(inp: GoalSetInput, ctx: Ctx) -> GoalView:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)
    if inp.objective is None and inp.status is None and inp.token_budget is None:
        raise ValidationError("goal-set requires objective, status, or token_budget")
    if inp.objective is None and await ctx.client.thread_goal_get(lane.id) is None:
        raise ValidationError(
            "goal-set requires objective when creating a goal; status and token_budget "
            "only update an existing goal"
        )
    goal = await ctx.client.thread_goal_set(
        lane.id,
        objective=inp.objective,
        status=inp.status,
        token_budget=inp.token_budget,
    )
    await ctx.registry.log_action("goal-set", lane=lane.id, detail=inp.objective)
    return GoalView(lane=lane.id, goal=_goal(goal))


async def goal_clear(inp: GoalClearInput, ctx: Ctx) -> GoalView:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)
    await ctx.client.thread_goal_clear(lane.id)
    await ctx.registry.log_action("goal-clear", lane=lane.id)
    return GoalView(lane=lane.id, goal=None)


async def fork(inp: ForkInput, ctx: Ctx) -> LaneRef:
    source = await _resolve(ctx, inp.lane)
    _require_writable(source)
    thread = await ctx.client.thread_fork(
        source.id,
        cwd=inp.cwd or source.cwd,
        sandbox=inp.sandbox,
        approval_policy=inp.approval_policy,
        approvals_reviewer=inp.approvals_reviewer,
        base_instructions=inp.base_instructions,
        developer_instructions=inp.developer_instructions,
        service_tier=inp.service_tier,
        model=inp.model,
        model_provider=inp.model_provider,
        ephemeral=inp.ephemeral,
    )
    handle = _handle(inp.name)
    lane = await ctx.registry.add_lane(
        id=thread.id,
        handle=handle,
        source="own",
        cwd=thread.cwd or inp.cwd or source.cwd,
        status="idle",
    )
    await ctx.registry.log_action("fork", lane=lane.id, detail=f"from {source.id}")
    try:
        await ctx.client.thread_set_name(thread.id, handle.removeprefix("@"))
    except ClientError as exc:
        ctx.log.warning("lane.name_set_failed", lane=lane.id, error=str(exc))
    return _ref(lane)


async def rollback(inp: RollbackInput, ctx: Ctx) -> LaneRef:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)
    await ctx.client.thread_rollback(lane.id, inp.turns)
    await ctx.registry.set_active_turn(lane.id, None)
    await ctx.registry.update_lane_status(lane.id, "idle")
    await ctx.registry.log_action("rollback", lane=lane.id, detail=f"{inp.turns} turn(s)")
    return _ref(await ctx.registry.get_lane(lane.id))


async def compact(inp: CompactInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane)
    await ctx.client.thread_compact_start(lane.id)
    await ctx.registry.log_action("compact", lane=lane.id)
    return ActionAck(lane=lane.id, op="compact")


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
