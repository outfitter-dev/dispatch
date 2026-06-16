"""Op handlers. Surface-agnostic: input + ctx in, output or raise out. They never
import CLI/MCP/socket types; side effects go through ``ctx`` (ADR-0006).

Authority guard (ADR-0005/0018): owned lanes are read/write; attached lanes can
be observed and have explicit metadata/lifecycle actions. Turn-writing and
history-mutating ops require an owned lane unless local policy explicitly allows
attached writes.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, time
from pathlib import Path
from typing import Literal, TypedDict, cast

from pydantic import ValidationError as PydanticValidationError

from outfitter.dispatch.client.errors import AppServerError as ClientAppServerError
from outfitter.dispatch.client.errors import ClientError
from outfitter.dispatch.client.models import (
    ThreadGoal,
    ThreadInfo,
    ThreadResult,
)
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    AuthorityError,
    DispatchError,
    NotFoundError,
    StagingError,
    ValidationError,
    project_error,
)
from outfitter.dispatch.registry.models import (
    Lane,
    LaneModelSettings,
    LaneSource,
    LaneStatus,
    LaneSync,
    ModelCatalogEntry,
    ServiceTierSource,
    SyncState,
)

from . import queue
from .history import detect_worktree, history_items_from_thread, summarize_history
from .launch import ResolvedLaunch, resolve_launch
from .model_registry import refresh_model_catalog, resolve_model_settings
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
    HistoryFileStat,
    HistoryInput,
    HistoryItem,
    HistoryOutput,
    HistoryThreadSummary,
    HistoryToolStat,
    LaneCapabilities,
    LaneDetail,
    LaneInput,
    LaneListItem,
    LaneRef,
    LaneRenameInput,
    LaneSyncInput,
    LaneSyncResult,
    LaneSyncView,
    LaneTextInput,
    LatestTurnView,
    LaunchInputSource,
    LaunchPlan,
    LaunchSettingsView,
    LogInput,
    LogOutput,
    ModelCatalogItem,
    ModelCatalogOutput,
    ModelConfigView,
    ModelServiceTierView,
    ModelsInput,
    NewInput,
    NewLane,
    OpenInput,
    RollbackInput,
    Roster,
    RosterInput,
    SearchInput,
    SearchMatch,
    SearchOutput,
    SendInput,
    ServiceTierView,
    ShowInput,
    StagedFile,
    StageView,
    StatusInput,
    StatusOutput,
    ThreadActionRef,
    ThreadModelView,
    ThreadTargetInput,
    TranscriptInput,
    TranscriptItem,
    TranscriptOutput,
    WatchEvent,
    WatchInput,
    WatchOutput,
)
from .selectors import resolve_managed_selector, resolve_thread_selector
from .staging import StageContent, stage_session
from .sync import scan_codex_jsonl
from .turn_settings import (
    load_turn_start_settings,
    runtime_settings_for_lane,
    thread_sandbox_to_turn_policy,
)
from .workspace import plan_workspace, prepare_workspace

_INTRO_TEMPLATE = '[dispatch] From {handle} ({ref}). Use `dispatch send {ref} "..."` to reply.'

# Bound attach metadata reads: if the app-server is wedged, fail clearly and never
# half-register (the registry write only follows a successful metadata read).
_ATTACH_METADATA_TIMEOUT_S = 10.0

_PREVIEW_MAX = 80


class _ManagedIdentityPayload(TypedDict):
    lane: str
    ref: str
    id: str
    title: str | None
    handle: str | None
    managed: bool
    source: LaneSource
    status: LaneStatus
    cwd: str | None
    writable: bool
    capabilities: LaneCapabilities
    write_locked_reason: str | None


_ATTACHED_WRITE_LOCK_REASON = (
    "attached thread; Dispatch does not own this App Server thread "
    "(enable policy.allow_attached_writes to override)"
)


def _can_write(lane: Lane, ctx: Ctx) -> bool:
    return lane.source == "own" or ctx.policy.allow_attached_writes


def _capabilities(lane: Lane, ctx: Ctx) -> LaneCapabilities:
    writable = _can_write(lane, ctx)
    return LaneCapabilities(
        send=writable,
        context=writable,
        steer=writable,
        queue=writable,
        interject=writable,
        goal_set=writable,
        goal_clear=writable,
        stop=writable,
        fork=writable,
        rollback=writable,
        compact=writable,
    )


def _write_locked_reason(lane: Lane, ctx: Ctx) -> str | None:
    if _can_write(lane, ctx):
        return None
    if lane.source == "attached":
        return _ATTACHED_WRITE_LOCK_REASON
    return "thread is not writable"


def _ref(lane: Lane, ctx: Ctx) -> LaneRef:
    return LaneRef(
        ref=lane.ref,
        id=lane.id,
        handle=lane.handle,
        source=lane.source,
        status=lane.status,
        cwd=lane.cwd,
        writable=_can_write(lane, ctx),
        capabilities=_capabilities(lane, ctx),
        write_locked_reason=_write_locked_reason(lane, ctx),
    )


def _action_ref(
    *,
    thread_id: str,
    lane: Lane | None = None,
    status: str | None = None,
) -> ThreadActionRef:
    if lane is None:
        return ThreadActionRef(id=thread_id, managed=False, source="unmanaged", status=status)
    return ThreadActionRef(
        ref=lane.ref,
        id=lane.id,
        handle=lane.handle,
        managed=True,
        source=lane.source,
        status=status or lane.status,
        cwd=lane.cwd,
    )


def _managed_identity(lane: Lane, ctx: Ctx) -> _ManagedIdentityPayload:
    return {
        "lane": lane.id,
        "ref": lane.ref,
        "id": lane.id,
        "title": lane.handle.removeprefix("@"),
        "handle": lane.handle,
        "managed": True,
        "source": lane.source,
        "status": lane.status,
        "cwd": lane.cwd,
        "writable": _can_write(lane, ctx),
        "capabilities": _capabilities(lane, ctx),
        "write_locked_reason": _write_locked_reason(lane, ctx),
    }


async def _apply_send_intro(inp: SendInput, ctx: Ctx) -> str:
    if not inp.intro:
        return inp.text

    sender_id = inp.caller_thread_id or os.environ.get("CODEX_THREAD_ID")
    if not sender_id:
        raise ValidationError("--intro requires CODEX_THREAD_ID from the current Codex thread")

    sender = await ctx.registry.find_lane(sender_id)
    if sender is None:
        raise ValidationError("--intro requires the current Codex thread to be managed by dispatch")

    intro = _INTRO_TEMPLATE.format(handle=sender.handle, ref=sender.ref)
    return f"{intro}\n\n{inp.text}"


def _sync_view(sync: LaneSync | None) -> LaneSyncView:
    if sync is None:
        return LaneSyncView()
    return LaneSyncView(
        state=sync.state,
        last_synced_at=sync.last_synced_at,
        source_path=sync.source_path,
        source_size=sync.source_size,
        latest_event_at=sync.latest_event_at,
        latest_turn_id=sync.latest_turn_id,
        transcript_partial=sync.transcript_partial,
        error=sync.error,
    )


def _latest_turn_view(lane: Lane) -> LatestTurnView:
    return LatestTurnView(
        id=lane.latest_turn_id,
        status=lane.latest_turn_status,
        error=lane.latest_error,
        error_at=lane.latest_error_at.isoformat() if lane.latest_error_at else None,
    )


def _model_view_from_values(
    *,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    requested_service_tier: str | None = None,
    resolved_service_tier: str | None = None,
    service_tier_name: str | None = None,
    service_tier_source: ServiceTierSource = "unknown",
) -> ThreadModelView:
    return ThreadModelView(
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        service_tier=ServiceTierView(
            requested=requested_service_tier,
            resolved=resolved_service_tier,
            name=service_tier_name,
            source=service_tier_source,
        ),
    )


def _model_view(
    settings: LaneModelSettings | None = None, sync: LaneSync | None = None
) -> ThreadModelView:
    if settings is not None:
        return _model_view_from_values(
            provider=settings.model_provider,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            requested_service_tier=settings.requested_service_tier,
            resolved_service_tier=settings.resolved_service_tier,
            service_tier_name=settings.service_tier_name,
            service_tier_source=settings.service_tier_source,
        )
    if sync is not None:
        return _model_view_from_values(
            provider=sync.model_provider,
            model=sync.model,
            reasoning_effort=sync.reasoning_effort,
            service_tier_source="observed"
            if sync.model_provider or sync.model or sync.reasoning_effort
            else "unknown",
        )
    return ThreadModelView()


def _model_view_from_thread(thread: ThreadInfo) -> ThreadModelView:
    has_model_data = any(
        (
            thread.model_provider,
            thread.model,
            thread.reasoning_effort,
            thread.service_tier,
        )
    )
    return _model_view_from_values(
        provider=thread.model_provider,
        model=thread.model,
        reasoning_effort=thread.reasoning_effort,
        resolved_service_tier=thread.service_tier,
        service_tier_source="observed" if has_model_data else "unknown",
    )


def _list_item(
    lane: Lane, sync: LaneSync | None, model: LaneModelSettings | None, ctx: Ctx
) -> LaneListItem:
    return LaneListItem(
        **_ref(lane, ctx).model_dump(),
        sync=_sync_view(sync),
        latest_turn=_latest_turn_view(lane),
        model=_model_view(model, sync),
    )


def _handle(name: str) -> str:
    return name if name.startswith("@") else f"@{name}"


async def _resolve(ctx: Ctx, ref: str) -> Lane:
    resolved = await resolve_managed_selector(ctx, ref, allow_fuzzy=False)
    if resolved.lane is None:
        raise NotFoundError(f"no managed thread {ref!r}")
    return resolved.lane


async def _find_lane(ctx: Ctx, ref: str) -> Lane | None:
    try:
        return (await resolve_managed_selector(ctx, ref, allow_fuzzy=False)).lane
    except NotFoundError:
        return None


async def _resolve_thread_target(ctx: Ctx, ref: str) -> tuple[str, Lane | None]:
    resolved = await resolve_thread_selector(ctx, ref, allow_unmanaged_raw=True, allow_fuzzy=False)
    return resolved.thread_id, resolved.lane


def _require_writable(lane: Lane, ctx: Ctx) -> None:
    if _can_write(lane, ctx):
        return
    if lane.source == "attached":
        raise AuthorityError(
            f"lane {lane.handle} ({lane.ref}) has source=attached and is read-only by "
            "local policy; attached lanes can be read, synced, and tailed, but turn-writing "
            "commands require an owned lane unless policy.allow_attached_writes is enabled. "
            "Use `dispatch new ...` for a writable owned lane, respond manually in Codex "
            "desktop, or set `[policy] allow_attached_writes = true` in the local Dispatch "
            "config to opt in."
        )
    raise AuthorityError(f"lane {lane.handle} ({lane.ref}) is not writable")


async def _prepare_attached_write(lane: Lane, ctx: Ctx) -> None:
    if lane.source == "attached":
        await ctx.client.thread_resume(lane.id, exclude_turns=True)


def _require_active_turn(lane: Lane, action: str) -> str:
    # App Server turn/interrupt and turn/steer both require a known turn id.
    if lane.active_turn_id is None:
        raise ValidationError(f"lane {lane.handle} has no active turn to {action}")
    return lane.active_turn_id


async def open_lane(inp: OpenInput, ctx: Ctx) -> LaneRef:
    thread = await ctx.client.thread_start(cwd=inp.cwd, sandbox="read-only", ephemeral=False)
    handle = _handle(inp.name)
    lane = await ctx.registry.add_lane(
        id=thread.id, handle=handle, source="own", cwd=inp.cwd, status="idle"
    )
    await ctx.registry.upsert_lane_runtime_settings(
        runtime_settings_for_lane(lane=lane.id, updated_at=ctx.registry.now_iso())
    )
    await ctx.registry.log_action("open", lane=lane.id, detail=handle)
    ctx.log.info("lane.open", lane=lane.id, handle=handle)
    return _ref(lane, ctx)


def _validate_launch(launch: ResolvedLaunch) -> None:
    """Reject launches the App Server would not honor, before any thread is created.

    Shared by ``new`` (real launch) and ``new-plan`` (dry-run preview) so a preview
    surfaces the same failure the launch would hit."""
    text = launch.text
    if text is not None and text.lstrip().startswith("/goal") and launch.goal is None:
        raise ValidationError(
            "`dispatch new` initial text sends plain message text; slash commands are not "
            "interpreted. Use `--goal`/`--goal-file` for a native dispatch/App Server goal."
        )
    if launch.goal is not None and launch.resolved.settings.ephemeral:
        raise ValidationError("native goals require non-ephemeral threads")


def _packet_str(launch: ResolvedLaunch) -> str | None:
    return str(launch.packet.path) if launch.packet is not None else None


def _stage_content(launch: ResolvedLaunch) -> StageContent:
    return StageContent(
        goal=launch.goal,
        prompt=launch.text,
        output_schema=launch.output_schema,
        base=launch.resolved.base_instructions,
        developer=launch.resolved.developer_instructions,
        config_text=launch.packet.config_text if launch.packet is not None else None,
        packet_path=launch.packet.path if launch.packet is not None else None,
    )


async def plan_new_lane(inp: NewInput, ctx: Ctx) -> LaunchPlan:
    """Resolve a launch and report what it would do — no daemon/thread mutation."""
    launch = resolve_launch(inp)
    _validate_launch(launch)
    workspace = plan_workspace(
        cwd=launch.resolved.cwd,
        name=launch.resolved.display_name,
        requested=inp.workspace,
        setup=inp.workspace_setup,
        worktree=inp.worktree,
        worktree_path=inp.worktree_path,
        worktree_branch=inp.worktree_branch,
        worktree_base=inp.worktree_base,
        config=launch.resolved.workspace,
        policy=ctx.policy,
    )
    s = launch.resolved.settings
    return LaunchPlan(
        name=launch.resolved.display_name,
        handle=launch.resolved.handle,
        cwd=str(workspace.effective_cwd),
        workspace=workspace.view,
        packet=_packet_str(launch),
        settings=LaunchSettingsView(
            sandbox=s.sandbox,
            approval_policy=s.approval_policy,
            approvals_reviewer=s.approvals_reviewer,
            model=s.model,
            model_provider=s.model_provider,
            effort=s.effort,
            summary=s.summary,
            personality=s.personality,
            service_tier=s.service_tier,
            ephemeral=bool(s.ephemeral),
        ),
        sources=[
            LaunchInputSource(
                slot=src.slot,
                origin=src.origin,
                path=src.path,
                bytes=src.bytes,
                sha256=src.sha256,
            )
            for src in launch.sources
        ],
        goal_set=launch.goal is not None,
        would_send=launch.would_send,
        output_schema_present=launch.output_schema is not None,
        stage=StageView(parts=list(launch.stage_plan.parts)),
        unknown_packet_files=launch.unknown_files,
        aux_packet_dirs=launch.aux_dirs,
    )


async def new_lane(inp: NewInput, ctx: Ctx) -> NewLane:
    launch = resolve_launch(inp)
    _validate_launch(launch)
    resolved = launch.resolved
    settings = resolved.settings
    goal = launch.goal
    workspace = await prepare_workspace(
        cwd=resolved.cwd,
        name=resolved.display_name,
        requested=inp.workspace,
        setup=inp.workspace_setup,
        worktree=inp.worktree,
        worktree_path=inp.worktree_path,
        worktree_branch=inp.worktree_branch,
        worktree_base=inp.worktree_base,
        config=resolved.workspace,
        policy=ctx.policy,
    )
    effective_cwd = workspace.effective_cwd
    resolved_model = await resolve_model_settings(
        ctx,
        model=settings.model,
        model_provider=settings.model_provider,
        reasoning_effort=settings.effort,
        service_tier=settings.service_tier,
    )
    explicit_service_tier = resolved_model.resolved_service_tier if settings.service_tier else None
    thread = await ctx.client.thread_start(
        cwd=str(effective_cwd),
        sandbox=settings.sandbox,
        approval_policy=settings.approval_policy,
        approvals_reviewer=settings.approvals_reviewer,
        base_instructions=resolved.base_instructions,
        developer_instructions=resolved.developer_instructions,
        personality=settings.personality,
        service_tier=explicit_service_tier,
        model=settings.model,
        model_provider=settings.model_provider,
        ephemeral=bool(settings.ephemeral),
    )
    lane = await ctx.registry.add_lane(
        id=thread.id, handle=resolved.handle, source="own", cwd=str(effective_cwd), status="idle"
    )
    lane_model = resolved_model.for_lane(lane.id, ctx.registry.now_iso())
    await ctx.registry.upsert_lane_model_settings(lane_model)
    await ctx.registry.upsert_lane_runtime_settings(
        runtime_settings_for_lane(
            lane=lane.id,
            updated_at=ctx.registry.now_iso(),
            sandbox=settings.sandbox,
            approval_policy=settings.approval_policy,
            approvals_reviewer=settings.approvals_reviewer,
            effort=settings.effort,
            summary=settings.summary,
            model=settings.model,
            service_tier=explicit_service_tier,
            output_schema=settings.output_schema,
            personality=settings.personality,
        )
    )
    await ctx.registry.log_action("new", lane=lane.id, detail=resolved.display_name)
    try:
        await ctx.client.thread_set_name(thread.id, resolved.display_name)
    except ClientError as exc:
        ctx.log.warning("lane.name_set_failed", lane=lane.id, error=str(exc))

    goal_set = False
    if goal is not None:
        try:
            await ctx.client.thread_goal_set(thread.id, objective=goal)
        except (DispatchError, ClientError) as exc:
            await ctx.registry.log_action(
                "goal-set",
                lane=lane.id,
                detail=goal[:120],
                outcome=project_error(exc).code,
            )
            raise
        await ctx.registry.log_action("goal-set", lane=lane.id, detail=goal[:120])
        goal_set = True

    staged = StageView()
    if launch.stage_plan.parts:
        try:
            result = await asyncio.to_thread(
                stage_session,
                cwd=effective_cwd,
                ref=lane.ref,
                lane_id=lane.id,
                plan=launch.stage_plan,
                content=_stage_content(launch),
            )
        except StagingError as exc:
            # Lane stays registered but is marked error; the first turn never starts
            # (staging is a pre-turn durability step). Surface the typed error.
            await ctx.registry.update_lane_status(lane.id, "error")
            await ctx.registry.log_action(
                "stage",
                lane=lane.id,
                detail=",".join(launch.stage_plan.parts),
                outcome=project_error(exc).code,
            )
            raise
        await ctx.registry.log_action(
            "stage", lane=lane.id, detail=",".join(launch.stage_plan.parts)
        )
        staged = StageView(
            parts=list(launch.stage_plan.parts),
            session_dir=str(result.session_dir),
            files=[
                StagedFile(part=e.part, path=e.path, bytes=e.bytes, sha256=e.sha256)
                for e in result.entries
            ],
        )

    message_accepted = False
    if settings.text is not None and inp.send:
        try:
            await ctx.registry.update_lane_status(lane.id, "busy")
            await ctx.client.turn_start(
                lane.id,
                settings.text,
                cwd=str(effective_cwd),
                approval_policy=settings.approval_policy,
                approvals_reviewer=settings.approvals_reviewer,
                sandbox_policy=(
                    thread_sandbox_to_turn_policy(settings.sandbox)
                    if settings.sandbox is not None
                    else None
                ),
                effort=settings.effort,
                summary=settings.summary,
                model=settings.model,
                service_tier=explicit_service_tier,
                output_schema=settings.output_schema,
                personality=settings.personality,
            )
        except (DispatchError, ClientError) as exc:
            await ctx.registry.record_turn_request_failed(lane.id, str(exc))
            await ctx.registry.log_action(
                "send",
                lane=lane.id,
                detail=settings.text[:120],
                outcome=project_error(exc).code,
            )
            raise
        await ctx.registry.log_action("send", lane=lane.id, detail=settings.text[:120])
        message_accepted = True
    ctx.log.info("lane.new", lane=lane.id, handle=lane.handle, message_accepted=message_accepted)
    ref = _ref(lane, ctx)
    return NewLane(
        **ref.model_dump(),
        message_accepted=message_accepted,
        goal_set=goal_set,
        staged=staged,
        workspace=workspace.view,
        latest_turn=_latest_turn_view(lane),
        model=_model_view(lane_model),
    )


async def attach_lane(inp: AttachInput, ctx: Ctx) -> LaneRef:
    existing = await ctx.registry.find_lane(inp.thread)
    if existing is not None:
        if inp.sync:
            await _sync_lane(existing, ctx, full=False)
        return _ref(existing, ctx)  # idempotent re-attach
    try:
        thread = await asyncio.wait_for(
            _read_thread_metadata(ctx, inp.thread), _ATTACH_METADATA_TIMEOUT_S
        )
    except TimeoutError as exc:
        # The registry write below never ran, so no lane is half-registered.
        raise AppServerError(
            f"attach timed out: thread/read metadata for {inp.thread!r} exceeded "
            f"{_ATTACH_METADATA_TIMEOUT_S:.0f}s (no lane registered)"
        ) from exc
    handle = thread.name or f"@{inp.thread[:8]}"
    sync = (
        await _sync_from_thread(thread.id, thread, full=False)
        if inp.sync
        else _metadata_sync(thread.id, thread, state="metadata")
    )
    lane, _ = await ctx.registry.add_lane_with_sync(
        id=thread.id,
        handle=handle,
        source="attached",
        cwd=thread.cwd,
        status="idle",
        sync=sync,
        audit_op="attach",
        audit_detail=handle,
    )
    await _record_observed_model(lane, thread, sync, ctx)
    ctx.log.info("lane.attach", lane=lane.id, handle=handle)
    return _ref(lane, ctx)


async def _read_thread_metadata(ctx: Ctx, thread_id: str) -> ThreadInfo:
    result = await ctx.client.thread_read(thread_id, include_turns=False)
    try:
        return ThreadResult.model_validate(result).thread
    except PydanticValidationError as exc:
        raise AppServerError(
            f"thread/read metadata for {thread_id!r} returned an invalid payload"
        ) from exc


async def _sync_lane(
    lane: Lane, ctx: Ctx, *, full: bool, metadata: ThreadInfo | None = None
) -> LaneSync:
    thread = metadata or await _read_thread_metadata(ctx, lane.id)
    sync = await ctx.registry.upsert_lane_sync(await _sync_from_thread(lane.id, thread, full=full))
    await _record_observed_model(lane, thread, sync, ctx)
    return sync


async def _record_observed_model(
    lane: Lane, thread: ThreadInfo, sync: LaneSync, ctx: Ctx
) -> LaneModelSettings | None:
    existing = await ctx.registry.get_lane_model_settings(lane.id)
    if existing is not None and existing.service_tier_source == "dispatch":
        return existing
    provider = sync.model_provider or thread.model_provider
    model = sync.model or thread.model
    reasoning_effort = sync.reasoning_effort or thread.reasoning_effort
    service_tier = thread.service_tier
    if not any((provider, model, reasoning_effort, service_tier)):
        return existing
    observed = LaneModelSettings(
        lane=lane.id,
        model_provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        requested_service_tier=None,
        resolved_service_tier=service_tier,
        service_tier_name=None,
        service_tier_source="observed",
        updated_at=ctx.registry.now_iso(),
    )
    await ctx.registry.upsert_lane_model_settings(observed)
    return observed


async def _sync_from_thread(lane_id: str, thread: ThreadInfo, *, full: bool) -> LaneSync:
    if thread.path is None:
        return _metadata_sync(lane_id, thread, state="metadata")
    facts = await asyncio.to_thread(scan_codex_jsonl, thread.path, full=full)
    state = facts.state
    return _metadata_sync(
        lane_id,
        thread,
        state=state,
        source_path=facts.source.path if facts.source else thread.path,
        source_device=facts.source.device if facts.source else None,
        source_inode=facts.source.inode if facts.source else None,
        source_size=facts.source.size if facts.source else None,
        source_mtime_ns=facts.source.mtime_ns if facts.source else None,
        line_count=facts.line_count,
        first_offset=facts.first_offset,
        tail_offset=facts.tail_offset,
        error=facts.error,
        cwd=facts.cwd,
        source=facts.source_kind,
        thread_source=facts.thread_source,
        model_provider=facts.model_provider,
        model=facts.model,
        reasoning_effort=facts.reasoning_effort,
        session_id=facts.session_id,
        latest_event_at=facts.latest_event_at,
        latest_turn_id=facts.latest_turn_id,
        transcript_partial=state != "complete",
    )


def _metadata_sync(
    lane_id: str,
    thread: ThreadInfo,
    *,
    state: SyncState,
    source_path: str | None = None,
    source_device: int | None = None,
    source_inode: int | None = None,
    source_size: int | None = None,
    source_mtime_ns: int | None = None,
    line_count: int | None = None,
    first_offset: int | None = None,
    tail_offset: int | None = None,
    error: str | None = None,
    cwd: str | None = None,
    source: str | None = None,
    thread_source: str | None = None,
    model_provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    session_id: str | None = None,
    latest_event_at: str | None = None,
    latest_turn_id: str | None = None,
    transcript_partial: bool = True,
) -> LaneSync:
    return LaneSync(
        lane=lane_id,
        state=state,
        source_path=source_path or thread.path,
        source_device=source_device,
        source_inode=source_inode,
        source_size=source_size,
        source_mtime_ns=source_mtime_ns,
        line_count=line_count,
        first_offset=first_offset,
        tail_offset=tail_offset,
        error=error,
        display_name=thread.name,
        preview=_short(thread.preview, limit=200),
        cwd=cwd or thread.cwd,
        source=source or thread.source,
        thread_source=thread_source or thread.thread_source,
        model_provider=model_provider or thread.model_provider,
        model=model or thread.model,
        reasoning_effort=reasoning_effort or thread.reasoning_effort,
        session_id=session_id or thread.session_id,
        latest_event_at=latest_event_at,
        latest_turn_id=latest_turn_id,
        transcript_partial=transcript_partial,
    )


async def send(inp: LaneTextInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    try:
        await _prepare_attached_write(lane, ctx)
        turn_settings = await load_turn_start_settings(ctx.registry, lane.id)
        await ctx.registry.update_lane_status(lane.id, "busy")
        await ctx.client.turn_start(
            lane.id,
            inp.text,
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
        await ctx.registry.record_turn_request_failed(lane.id, str(exc))
        await ctx.registry.log_action(
            "send", lane=lane.id, detail=inp.text[:120], outcome=project_error(exc).code
        )
        raise
    await ctx.registry.log_action("send", lane=lane.id, detail=inp.text[:120])
    return ActionAck(**_managed_identity(lane, ctx), op="send")


async def send_message(inp: SendInput, ctx: Ctx) -> ActionAck:
    text = await _apply_send_intro(inp, ctx)
    match inp.mode:
        case "send":
            return await send(LaneTextInput(lane=inp.lane, text=text), ctx)
        case "steer":
            return await steer(LaneTextInput(lane=inp.lane, text=text), ctx)
        case "context":
            return await brief(LaneTextInput(lane=inp.lane, text=text), ctx)
        case "interject":
            lane = await _resolve(ctx, inp.lane)
            _require_writable(lane, ctx)
            turn_id = _require_active_turn(lane, "interject")
            try:
                await _prepare_attached_write(lane, ctx)
                await ctx.client.turn_interrupt(lane.id, turn_id)
                await ctx.registry.log_action("interrupt", lane=lane.id, detail="interject")
                turn_settings = await load_turn_start_settings(ctx.registry, lane.id)
                await ctx.registry.update_lane_status(lane.id, "busy")
                await ctx.client.turn_start(
                    lane.id,
                    text,
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
                await ctx.registry.record_turn_request_failed(lane.id, str(exc))
                await ctx.registry.log_action(
                    "send", lane=lane.id, detail=text[:120], outcome=project_error(exc).code
                )
                raise
            await ctx.registry.log_action("send", lane=lane.id, detail=text[:120])
            return ActionAck(**_managed_identity(lane, ctx), op="interject")
        case "queue":
            lane = await _resolve(ctx, inp.lane)
            _require_writable(lane, ctx)
            message = await ctx.registry.enqueue_message(lane=lane.id, text=text)
            await ctx.registry.log_action("queue", lane=lane.id, detail=text[:120])
            if lane.status == "idle":
                await queue.drain_next_queued_message(ctx, lane.id)
            pending = await ctx.registry.pending_message_count(lane.id)
            return ActionAck(
                **_managed_identity(lane, ctx),
                op="queue",
                detail=f"queued message {message.id}; pending={pending}",
            )


async def steer(inp: LaneTextInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    turn_id = _require_active_turn(lane, "steer")
    await _prepare_attached_write(lane, ctx)
    await ctx.client.turn_steer(lane.id, turn_id, inp.text)
    await ctx.registry.log_action("steer", lane=lane.id, detail=inp.text[:120])
    return ActionAck(**_managed_identity(lane, ctx), op="steer")


async def brief(inp: LaneTextInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    item: dict[str, object] = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": inp.text}],
    }
    await _prepare_attached_write(lane, ctx)
    await ctx.client.inject_items(lane.id, [item])
    await ctx.registry.log_action("brief", lane=lane.id, detail=inp.text[:120])
    return ActionAck(**_managed_identity(lane, ctx), op="brief")


async def interrupt(inp: LaneInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    turn_id = _require_active_turn(lane, "interrupt")
    await _prepare_attached_write(lane, ctx)
    await ctx.client.turn_interrupt(lane.id, turn_id)
    await ctx.registry.log_action("interrupt", lane=lane.id)
    return ActionAck(**_managed_identity(lane, ctx), op="interrupt")


async def stop(inp: LaneInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    turn_id = _require_active_turn(lane, "stop")
    await _prepare_attached_write(lane, ctx)
    await ctx.client.turn_interrupt(lane.id, turn_id)
    await ctx.registry.log_action("stop", lane=lane.id)
    return ActionAck(**_managed_identity(lane, ctx), op="stop")


async def show(inp: ShowInput, ctx: Ctx) -> LaneDetail:
    resolved = await resolve_managed_selector(ctx, inp.lane, allow_fuzzy=True)
    if resolved.lane is None:
        raise NotFoundError(f"no managed thread {inp.lane!r}")
    lane = resolved.lane
    sync = await ctx.registry.get_lane_sync(lane.id)
    model_settings = await ctx.registry.get_lane_model_settings(lane.id)
    transcript: list[TranscriptItem] = []
    if inp.include_transcript:
        result = await ctx.client.thread_read(lane.id, include_turns=True)
        transcript = _transcript_from_thread(result, limit=inp.max_items)
    return LaneDetail(
        **_ref(lane, ctx).model_dump(),
        active_turn_id=lane.active_turn_id,
        latest_turn=_latest_turn_view(lane),
        sync=_sync_view(sync),
        model=_model_view(model_settings, sync),
        transcript=transcript,
    )


async def sync_lane(inp: LaneSyncInput, ctx: Ctx) -> LaneSyncResult:
    lane = await _resolve(ctx, inp.lane)
    sync = await _sync_lane(lane, ctx, full=inp.full)
    model_settings = await ctx.registry.get_lane_model_settings(lane.id)
    await ctx.registry.log_action(
        "sync", lane=lane.id, detail=f"state={sync.state}; full={inp.full}"
    )
    return LaneSyncResult(
        **_managed_identity(lane, ctx),
        sync=_sync_view(sync),
        model=_model_view(model_settings, sync),
    )


async def rename_lane(inp: LaneRenameInput, ctx: Ctx) -> ThreadActionRef:
    thread_id, lane = await _resolve_thread_target(ctx, inp.old)
    if lane is None:
        await ctx.client.thread_set_name(thread_id, inp.new.removeprefix("@"))
        await ctx.registry.log_action("lane-rename", lane=thread_id, detail=inp.new)
        return _action_ref(thread_id=thread_id)

    handle = _handle(inp.new)
    existing = await ctx.registry.find_lane_by_handle(handle)
    if existing is not None and existing.id != lane.id:
        raise ValidationError(f"lane handle {handle!r} is already registered")
    await ctx.client.thread_set_name(lane.id, handle.removeprefix("@"))
    await ctx.registry.update_lane_handle(lane.id, handle)
    await ctx.registry.log_action("lane-rename", lane=lane.id, detail=handle)
    return _action_ref(thread_id=lane.id, lane=await ctx.registry.get_lane(lane.id))


async def watch(inp: WatchInput, ctx: Ctx) -> WatchOutput:
    resolved = await resolve_managed_selector(ctx, inp.lane, allow_fuzzy=True)
    if resolved.lane is None:
        raise NotFoundError(f"no managed thread {inp.lane!r}")
    lane = resolved.lane
    if inp.timeout == 0:
        return WatchOutput(**_managed_identity(lane, ctx), events=[], timed_out=True)
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
    return WatchOutput(**_managed_identity(lane, ctx), events=events, timed_out=timed_out)


async def transcript(inp: TranscriptInput, ctx: Ctx) -> TranscriptOutput:
    resolved = await resolve_managed_selector(ctx, inp.lane, allow_fuzzy=True)
    if resolved.lane is None:
        raise NotFoundError(f"no managed thread {inp.lane!r}")
    lane = resolved.lane
    result = await ctx.client.thread_read(lane.id, include_turns=True)
    return TranscriptOutput(
        **_managed_identity(lane, ctx),
        items=_transcript_from_thread(result, limit=inp.limit),
    )


async def history(inp: HistoryInput, ctx: Ctx) -> HistoryOutput:
    mode = _history_mode(inp)
    if mode == "overview":
        if inp.lane is not None:
            raise ValidationError("history overview does not accept a thread selector")
        summaries: list[HistoryThreadSummary] = []
        for lane in await ctx.registry.list_lanes():
            summary = await _history_summary_for_lane(lane, ctx)
            if _history_summary_matches(summary, inp):
                summaries.append(summary)
            if len(summaries) >= inp.limit:
                break
        return HistoryOutput(mode="overview", threads=summaries)

    if inp.lane is None:
        raise ValidationError("history view requires a thread selector")
    lane = await _resolve(ctx, inp.lane)
    result = await ctx.client.thread_read(lane.id, include_turns=True)
    summary, _items, tools, files = await _history_details(lane, result, ctx)
    if mode == "summary":
        return HistoryOutput(mode="summary", thread=summary, tools=tools, files=files)
    if mode == "tools":
        return HistoryOutput(mode="tools", thread=summary, tools=tools[: inp.limit])
    if mode == "files":
        return HistoryOutput(mode="files", thread=summary, files=files[: inp.limit])
    return HistoryOutput(
        mode="items",
        thread=summary,
        items=history_items_from_thread(
            result,
            item_type=inp.item_type,
            tool=inp.tool,
            grep=inp.grep,
            raw=inp.raw,
            limit=inp.limit,
        ),
    )


def _history_mode(inp: HistoryInput) -> Literal["overview", "summary", "items", "tools", "files"]:
    if inp.view == "auto":
        return "overview" if inp.lane is None else "summary"
    return inp.view


def _history_summary_matches(summary: HistoryThreadSummary, inp: HistoryInput) -> bool:
    if inp.cwd is not None and inp.cwd.casefold() not in (summary.cwd or "").casefold():
        return False
    if inp.source is not None and summary.source != inp.source:
        return False
    if inp.status is not None and summary.status != inp.status:
        return False
    if inp.has_tool is not None and not any(
        inp.has_tool.casefold() in tool.casefold() for tool in summary.unique_tools
    ):
        return False
    if inp.changed is not None and summary.worktree.dirty != inp.changed:
        return False
    return not (
        inp.min_bytes is not None
        and (summary.transcript_bytes is None or summary.transcript_bytes < inp.min_bytes)
    )


async def _history_summary_for_lane(lane: Lane, ctx: Ctx) -> HistoryThreadSummary:
    result = await ctx.client.thread_read(lane.id, include_turns=True)
    summary, _items, _tools, _files = await _history_details(lane, result, ctx)
    return summary


async def _history_details(
    lane: Lane, result: dict[str, object], ctx: Ctx
) -> tuple[HistoryThreadSummary, list[HistoryItem], list[HistoryToolStat], list[HistoryFileStat]]:
    sync = await ctx.registry.get_lane_sync(lane.id)
    worktree = await detect_worktree(lane.cwd)
    return summarize_history(result, lane=lane, sync=sync, worktree=worktree)


async def search(inp: SearchInput, ctx: Ctx) -> SearchOutput:
    if inp.managed and inp.unmanaged:
        raise ValidationError("search can filter --managed or --unmanaged, not both")
    if inp.limit > inp.max_scan:
        raise ValidationError("search limit cannot exceed max_scan")

    lane_map = {lane.id: lane for lane in await ctx.registry.list_lanes(include_archived=True)}
    root_filters = _search_roots(inp)
    since = _parse_bound(inp.since, start=True)
    until = _parse_bound(inp.until, start=False)

    if inp.lane is not None:
        return await _search_one_thread(inp, ctx, lane_map, root_filters, since, until)

    matches: list[SearchMatch] = []
    scanned = 0
    cursor: str | None = None
    next_cursor: str | None = None
    page_limit = min(max(inp.limit * 4, 20), 100)
    while len(matches) < inp.limit and scanned < inp.max_scan:
        response = await ctx.client.thread_search(
            inp.query,
            archived=inp.archived,
            cursor=cursor,
            limit=min(page_limit, inp.max_scan - scanned),
            sort_direction="asc" if inp.ascending else "desc",
            sort_key=inp.sort,
        )
        if not response.data:
            next_cursor = response.next_cursor
            break
        for candidate in response.data:
            scanned += 1
            match = _search_match(
                candidate.thread,
                candidate.snippet,
                lane_map=lane_map,
                managed_only=inp.managed,
                unmanaged_only=inp.unmanaged,
                roots=root_filters,
                date_field=inp.date_field,
                since=since,
                until=until,
            )
            if match is not None:
                matches.append(match)
            if len(matches) >= inp.limit or scanned >= inp.max_scan:
                break
        cursor = response.next_cursor
        next_cursor = response.next_cursor
        if cursor is None:
            break

    return SearchOutput(
        query=inp.query,
        matches=matches,
        scanned=scanned,
        next_cursor=next_cursor,
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


async def _search_one_thread(
    inp: SearchInput,
    ctx: Ctx,
    lane_map: dict[str, Lane],
    roots: tuple[Path, ...],
    since: float | None,
    until: float | None,
) -> SearchOutput:
    assert inp.lane is not None
    resolved = await resolve_thread_selector(
        ctx, inp.lane, allow_unmanaged_raw=True, allow_fuzzy=True
    )
    thread_id = resolved.thread_id
    result = await ctx.client.thread_read(thread_id, include_turns=True)
    try:
        thread = ThreadResult.model_validate(result).thread
    except PydanticValidationError as exc:
        raise AppServerError(
            f"thread/read transcript for {thread_id!r} returned an invalid payload"
        ) from exc

    if inp.managed and thread.id not in lane_map:
        return SearchOutput(query=inp.query, matches=[], scanned=0)
    if inp.unmanaged and thread.id in lane_map:
        return SearchOutput(query=inp.query, matches=[], scanned=0)
    if _outside_roots(thread.cwd, roots) or _outside_date(
        thread, inp.date_field, since=since, until=until
    ):
        return SearchOutput(query=inp.query, matches=[], scanned=0)

    query = inp.query.casefold()
    items = _transcript_from_thread(result, limit=inp.max_scan)
    matches: list[SearchMatch] = []
    scanned = 0
    for item in items:
        scanned += 1
        if item.text is None or query not in item.text.casefold():
            continue
        match = _search_match(
            thread,
            _short(item.text, limit=200) or "",
            lane_map=lane_map,
            managed_only=False,
            unmanaged_only=False,
            roots=(),
            date_field=inp.date_field,
            since=None,
            until=None,
        )
        if match is not None:
            matches.append(match)
        if len(matches) >= inp.limit:
            break
    return SearchOutput(query=inp.query, matches=matches, scanned=scanned)


def _search_roots(inp: SearchInput) -> tuple[Path, ...]:
    roots: list[Path] = []
    if inp.directory is not None:
        roots.append(_normalize_path(inp.directory))
    if inp.repo is not None:
        roots.append(_repo_root(inp.repo))
    return tuple(roots)


def _normalize_path(path: str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _repo_root(path: str) -> Path:
    current = _normalize_path(path)
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ValidationError(f"no git repo found at or above {path!r}")


def _parse_bound(value: str | None, *, start: bool) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise ValidationError("date bound cannot be empty")
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            day = datetime.fromisoformat(text).date()
            dt = datetime.combine(day, time.min if start else time.max)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"invalid ISO date/time: {value!r}") from exc
    return dt.timestamp()


def _search_match(
    thread: ThreadInfo,
    snippet: str,
    *,
    lane_map: dict[str, Lane],
    managed_only: bool,
    unmanaged_only: bool,
    roots: tuple[Path, ...],
    date_field: str,
    since: float | None,
    until: float | None,
) -> SearchMatch | None:
    lane = lane_map.get(thread.id)
    if managed_only and lane is None:
        return None
    if unmanaged_only and lane is not None:
        return None
    if _outside_roots(thread.cwd, roots):
        return None
    if _outside_date(thread, date_field, since=since, until=until):
        return None

    source = lane.source if lane is not None else "unmanaged"
    return SearchMatch(
        id=thread.id,
        ref=lane.ref if lane is not None else None,
        handle=lane.handle if lane is not None else None,
        managed=lane is not None,
        source=source,
        status=lane.status if lane is not None else (thread.status.type if thread.status else None),
        name=thread.name,
        cwd=thread.cwd,
        preview=_short(thread.preview),
        snippet=snippet,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _outside_roots(cwd: str | None, roots: tuple[Path, ...]) -> bool:
    if not roots:
        return False
    if cwd is None:
        return True
    path = _normalize_path(cwd)
    return not all(path == root or path.is_relative_to(root) for root in roots)


def _outside_date(
    thread: ThreadInfo, date_field: str, *, since: float | None, until: float | None
) -> bool:
    if since is None and until is None:
        return False
    timestamp = thread.created_at if date_field == "created_at" else thread.updated_at
    if timestamp is None:
        return True
    return (since is not None and timestamp < since) or (until is not None and timestamp > until)


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
    resolved = await resolve_managed_selector(ctx, inp.lane, allow_fuzzy=True)
    if resolved.lane is None:
        raise NotFoundError(f"no managed thread {inp.lane!r}")
    lane = resolved.lane
    goal = await ctx.client.thread_goal_get(lane.id)
    return GoalView(**_managed_identity(lane, ctx), goal=_goal(goal) if goal is not None else None)


async def goal_set(inp: GoalSetInput, ctx: Ctx) -> GoalView:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    await _prepare_attached_write(lane, ctx)
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
    return GoalView(**_managed_identity(lane, ctx), goal=_goal(goal))


async def goal_clear(inp: GoalClearInput, ctx: Ctx) -> GoalView:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    await _prepare_attached_write(lane, ctx)
    await ctx.client.thread_goal_clear(lane.id)
    await ctx.registry.log_action("goal-clear", lane=lane.id)
    return GoalView(**_managed_identity(lane, ctx), goal=None)


async def fork(inp: ForkInput, ctx: Ctx) -> LaneRef:
    source = await _resolve(ctx, inp.lane)
    _require_writable(source, ctx)
    await _prepare_attached_write(source, ctx)
    resolved_model = await resolve_model_settings(
        ctx,
        model=inp.model,
        model_provider=inp.model_provider,
        reasoning_effort=None,
        service_tier=inp.service_tier,
    )
    explicit_service_tier = resolved_model.resolved_service_tier if inp.service_tier else None
    thread = await ctx.client.thread_fork(
        source.id,
        cwd=inp.cwd or source.cwd,
        sandbox=inp.sandbox,
        approval_policy=inp.approval_policy,
        approvals_reviewer=inp.approvals_reviewer,
        base_instructions=inp.base_instructions,
        developer_instructions=inp.developer_instructions,
        service_tier=explicit_service_tier,
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
    await ctx.registry.upsert_lane_model_settings(
        resolved_model.for_lane(lane.id, ctx.registry.now_iso())
    )
    await ctx.registry.upsert_lane_runtime_settings(
        runtime_settings_for_lane(
            lane=lane.id,
            updated_at=ctx.registry.now_iso(),
            sandbox=inp.sandbox or "read-only",
            approval_policy=inp.approval_policy or "never",
            approvals_reviewer=inp.approvals_reviewer,
            model=inp.model,
            service_tier=explicit_service_tier,
        )
    )
    await ctx.registry.log_action("fork", lane=lane.id, detail=f"from {source.id}")
    try:
        await ctx.client.thread_set_name(thread.id, handle.removeprefix("@"))
    except ClientError as exc:
        ctx.log.warning("lane.name_set_failed", lane=lane.id, error=str(exc))
    return _ref(lane, ctx)


async def rollback(inp: RollbackInput, ctx: Ctx) -> LaneRef:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    await _prepare_attached_write(lane, ctx)
    await ctx.client.thread_rollback(lane.id, inp.turns)
    await ctx.registry.set_active_turn(lane.id, None)
    await ctx.registry.update_lane_status(lane.id, "idle")
    await ctx.registry.log_action("rollback", lane=lane.id, detail=f"{inp.turns} turn(s)")
    return _ref(await ctx.registry.get_lane(lane.id), ctx)


async def compact(inp: CompactInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve(ctx, inp.lane)
    _require_writable(lane, ctx)
    await _prepare_attached_write(lane, ctx)
    await ctx.client.thread_compact_start(lane.id)
    await ctx.registry.log_action("compact", lane=lane.id)
    return ActionAck(**_managed_identity(lane, ctx), op="compact")


async def roster(inp: RosterInput, ctx: Ctx) -> Roster:
    lanes = await ctx.registry.list_lanes(include_archived=inp.include_archived)
    syncs = await ctx.registry.get_lane_sync_many([lane.id for lane in lanes])
    models = await ctx.registry.get_lane_model_settings_many([lane.id for lane in lanes])
    return Roster(
        lanes=[_list_item(lane, syncs.get(lane.id), models.get(lane.id), ctx) for lane in lanes]
    )


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
        model=_model_view_from_thread(thread),
    )


async def discover(inp: DiscoverInput, ctx: Ctx) -> Discovery:
    """List persisted Codex sessions (``thread/list``, state-db only) — read-only and
    distinct from ``roster``: these are candidates to ``attach``, not managed lanes.
    Discovery does not resume or register anything."""
    threads = await ctx.client.thread_list(
        limit=inp.limit,
        archived=False,
        sort_direction="desc",
        sort_key="updated_at",
        use_state_db_only=True,
    )
    return Discovery(sessions=[_session(thread) for thread in threads])


async def models(inp: ModelsInput, ctx: Ctx) -> ModelCatalogOutput:
    refreshed_at: str | None
    if inp.refresh:
        snapshot = await refresh_model_catalog(ctx)
        entries = snapshot.models
        config = snapshot.config
        refreshed_at = snapshot.refreshed_at
        source = "app-server"
    else:
        config = await ctx.client.config_read()
        entries = await ctx.registry.list_model_catalog()
        source = "registry"
        refreshed_at = max((entry.last_seen_at for entry in entries), default=None)
    if not inp.include_hidden:
        entries = [entry for entry in entries if not entry.hidden]
    catalog_state: Literal["ready", "empty"] = "empty" if not entries else "ready"
    hint = (
        "run dispatch models without --no-refresh to refresh the App Server model catalog"
        if catalog_state == "empty" and not inp.refresh
        else None
    )
    return ModelCatalogOutput(
        refreshed_at=refreshed_at,
        source=source,
        catalog_state=catalog_state,
        hint=hint,
        configured_default=ModelConfigView(
            model=config.model,
            model_provider=config.model_provider,
            service_tier=config.service_tier,
            model_reasoning_effort=config.model_reasoning_effort,
        ),
        models=[_model_catalog_item(entry) for entry in entries],
    )


def _model_catalog_item(entry: ModelCatalogEntry) -> ModelCatalogItem:
    aliases: dict[str, str] = {}
    for tier in entry.service_tiers:
        if tier.name.lower() == "fast":
            aliases["fast"] = tier.id
    if "fast" not in aliases and any(
        tier.lower() == "fast" for tier in entry.additional_speed_tiers
    ):
        aliases["fast"] = "fast"
    return ModelCatalogItem(
        id=entry.id,
        provider=entry.provider,
        display_name=entry.display_name,
        description=entry.description,
        is_default=entry.is_default,
        hidden=entry.hidden,
        default_reasoning_effort=entry.default_reasoning_effort,
        supported_reasoning_efforts=entry.supported_reasoning_efforts,
        default_service_tier=entry.default_service_tier,
        service_tiers=[
            ModelServiceTierView(
                id=tier.id,
                name=tier.name,
                description=tier.description,
            )
            for tier in entry.service_tiers
        ],
        aliases=aliases,
        last_seen_at=entry.last_seen_at,
    )


async def archive(inp: ThreadTargetInput, ctx: Ctx) -> ThreadActionRef:
    thread_id, lane = await _resolve_thread_target(ctx, inp.target)
    try:
        await ctx.client.thread_archive(thread_id)
    except ClientAppServerError as exc:
        if lane is None or not _is_no_rollout_archive_error(exc):
            raise
        ctx.log.info("lane.archive_local_no_rollout", lane=thread_id)
    if lane is not None:
        await ctx.registry.update_lane_status(lane.id, "archived")
        lane = await ctx.registry.get_lane(lane.id)
    await ctx.registry.log_action("archive", lane=thread_id)
    return _action_ref(thread_id=thread_id, lane=lane, status="archived")


def _is_no_rollout_archive_error(exc: ClientAppServerError) -> bool:
    return exc.code == -32600 and "no rollout found" in exc.message.lower()


async def restore(inp: ThreadTargetInput, ctx: Ctx) -> ThreadActionRef:
    thread_id, lane = await _resolve_thread_target(ctx, inp.target)
    thread = await ctx.client.thread_unarchive(thread_id)
    status = _lane_status(thread)
    if lane is not None:
        await ctx.registry.update_lane_status(lane.id, status)
        lane = await ctx.registry.get_lane(lane.id)
    await ctx.registry.log_action("restore", lane=thread_id)
    return _action_ref(thread_id=thread_id, lane=lane, status=status)


def _lane_status(thread: ThreadInfo) -> LaneStatus:
    status = thread.status.type if thread.status is not None else None
    if status in {"idle", "busy", "waiting_approval", "archived", "error", "unknown"}:
        return cast(LaneStatus, status)
    return "unknown"


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
