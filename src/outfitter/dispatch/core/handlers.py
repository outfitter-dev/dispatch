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
import time as time_module
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time
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
from outfitter.dispatch.core.capture import bound_text
from outfitter.dispatch.registry.models import (
    HistoryCapability,
    InboxMessage,
    Lane,
    LaneModelSettings,
    LaneSource,
    LaneStatus,
    LaneSync,
    MessageReceipt,
    ModelCatalogEntry,
    ServerRequest,
    ServiceTierSource,
    Subscription,
    SubscriptionAckPolicy,
    SubscriptionDeliverPolicy,
    SubscriptionDelivery,
    SubscriptionWhen,
    SyncState,
    ThreadItem,
    ThreadItemRef,
)

from . import queue
from .backfill import backfill_codex_history
from .capacity import refresh_codex_capacity
from .history import (
    detect_worktree,
    history_items_from_indexed,
    history_items_from_thread,
    history_rollups_from_indexed,
)
from .history_index import index_codex_thread_read
from .launch import ResolvedLaunch, resolve_launch
from .message_attribution import codex_thread_link, render_dispatch_message
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
    HistoryOutput,
    HistoryThreadSummary,
    HistoryToolStat,
    InboxAckInput,
    InboxAckResult,
    InboxList,
    InboxListInput,
    InboxMessageView,
    InboxReadInput,
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
    PermissionProfileItem,
    PermissionProfilesInput,
    PermissionProfilesOutput,
    QueryInput,
    QueryMatch,
    QueryOutput,
    QueryRef,
    RollbackInput,
    Roster,
    RosterInput,
    SearchInput,
    SearchMatch,
    SearchOutput,
    SendInput,
    ServerRequestList,
    ServerRequestListInput,
    ServerRequestRespondInput,
    ServerRequestRespondResult,
    ServerRequestView,
    ServiceTierView,
    ShowInput,
    StagedFile,
    StageView,
    StatusInput,
    StatusOutput,
    SubscribeInput,
    SubscriptionIdInput,
    SubscriptionList,
    SubscriptionListInput,
    SubscriptionRemoved,
    SubscriptionView,
    ThreadActionRef,
    ThreadModelView,
    ThreadTargetInput,
    ThreadTopologyView,
    TranscriptInput,
    TranscriptItem,
    TranscriptOutput,
    UsageInput,
    UsageObservationView,
    UsageOutput,
    UsageWindowView,
    WatchEvent,
    WatchInput,
    WatchOutput,
)
from .permission_profiles import refresh_permission_profiles, resolve_permission_profile
from .selectors import resolve_managed_selector, resolve_thread_selector
from .server_request_policy import expected_response
from .server_requests import respond_to_server_request
from .staging import StageContent, stage_session
from .sync import SourceIdentity, SyncLimits, scan_codex_jsonl
from .topology import observe_thread, observe_threads, topology_views
from .turn_settings import (
    load_turn_start_settings,
    runtime_settings_for_lane,
    thread_sandbox_to_turn_policy,
)
from .workspace import plan_workspace, prepare_workspace

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


class _SubscriptionSettings(TypedDict):
    when: SubscriptionWhen
    to: str
    delivery: SubscriptionDelivery
    deliver: SubscriptionDeliverPolicy
    tail: int
    once: bool
    ack: SubscriptionAckPolicy
    attribution: bool


@dataclass(frozen=True)
class _IndexedHistory:
    indexed: list[ThreadItem]
    refs: dict[tuple[str, str, str], list[ThreadItemRef]]


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

    return render_dispatch_message(
        body=inp.text,
        kind="dm",
        source=codex_thread_link(sender.handle, sender.id),
        ref=sender.ref,
        details=(f'reply `dispatch send {sender.ref} "..."`',),
    )


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
        history_source=sync.history_source,
        history_complete=sync.history_complete,
        history_capability=sync.history_capability,
        observation_enabled=sync.observation_enabled,
        pages_scanned=sync.pages_scanned,
        turns_indexed=sync.turns_indexed,
        items_indexed=sync.items_indexed,
        unchanged_skipped=sync.unchanged_skipped,
        scanned_bytes=sync.scanned_bytes,
        duration_ms=sync.duration_ms,
        truncated=sync.truncated,
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
    lane: Lane,
    sync: LaneSync | None,
    model: LaneModelSettings | None,
    ctx: Ctx,
    topology: ThreadTopologyView | None = None,
) -> LaneListItem:
    return LaneListItem(
        **_ref(lane, ctx).model_dump(),
        sync=_sync_view(sync),
        latest_turn=_latest_turn_view(lane),
        model=_model_view(model, sync),
        topology=topology or ThreadTopologyView(),
    )


def _handle(name: str) -> str:
    return name if name.startswith("@") else f"@{name}"


async def _resolve(ctx: Ctx, ref: str) -> Lane:
    resolved = await resolve_managed_selector(ctx, ref, allow_fuzzy=False)
    if resolved.lane is None:
        raise NotFoundError(f"no managed thread {ref!r}")
    return resolved.lane


async def _resolve_message_target(ctx: Ctx, ref: str) -> Lane:
    resolved = await resolve_thread_selector(ctx, ref, allow_unmanaged_raw=True, allow_fuzzy=False)
    if resolved.lane is not None:
        return resolved.lane
    try:
        thread = await asyncio.wait_for(
            _read_thread_metadata(ctx, resolved.thread_id), _ATTACH_METADATA_TIMEOUT_S
        )
    except TimeoutError as exc:
        raise AppServerError(
            f"send timed out: thread/read metadata for {resolved.thread_id!r} exceeded "
            f"{_ATTACH_METADATA_TIMEOUT_S:.0f}s (no lane registered)"
        ) from exc
    lane = await _register_attached_thread(thread, ctx, sync=True, audit_op="send-manage")
    ctx.log.info("lane.send_manage", lane=lane.id, handle=lane.handle)
    return lane


async def _find_lane(ctx: Ctx, ref: str) -> Lane | None:
    try:
        return (await resolve_managed_selector(ctx, ref, allow_fuzzy=False)).lane
    except NotFoundError:
        return None


async def _resolve_self(ctx: Ctx, caller_thread_id: str | None) -> Lane:
    thread_id = caller_thread_id or os.environ.get("CODEX_THREAD_ID")
    if not thread_id:
        raise ValidationError("self requires CODEX_THREAD_ID from the current Codex thread")
    lane = await ctx.registry.find_lane(thread_id)
    if lane is None:
        raise ValidationError("self requires the current Codex thread to be managed by dispatch")
    return lane


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
    await observe_thread(
        ctx.registry, thread, lifecycle_state="active", relationship_source="thread/start"
    )
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
            permission_profile=s.permission_profile,
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
    permission_profile = await resolve_permission_profile(
        ctx, settings.permission_profile, cwd=str(effective_cwd)
    )
    thread = await ctx.client.thread_start(
        cwd=str(effective_cwd),
        permission_profile=permission_profile,
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
    await observe_thread(
        ctx.registry, thread, lifecycle_state="active", relationship_source="thread/start"
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
            permission_profile=permission_profile,
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
                permission_profile=permission_profile,
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
            error = _bounded_error(exc, ctx)
            await ctx.registry.record_turn_request_failed(lane.id, error)
            await _record_direct_send_receipt(ctx, lane, status="failed", error=error)
            await ctx.registry.log_action(
                "send",
                lane=lane.id,
                detail=settings.text[:120],
                outcome=project_error(exc).code,
            )
            raise
        await _record_direct_send_receipt(ctx, lane, status="sent")
        await ctx.registry.log_action("send", lane=lane.id, detail=settings.text[:120])
        message_accepted = True
    subscription = None
    if inp.subscribe is not None:
        subscription = await subscribe(
            SubscribeInput(
                target=lane.ref,
                spec=inp.subscribe,
                caller_thread_id=inp.caller_thread_id,
            ),
            ctx,
        )
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
        subscription=subscription,
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
    lane = await _register_attached_thread(thread, ctx, sync=inp.sync, audit_op="attach")
    ctx.log.info("lane.attach", lane=lane.id, handle=lane.handle)
    return _ref(lane, ctx)


async def _register_attached_thread(
    thread: ThreadInfo, ctx: Ctx, *, sync: bool, audit_op: str
) -> Lane:
    await observe_thread(ctx.registry, thread, relationship_source="thread/read")
    handle = thread.name or f"@{thread.id[:8]}"
    lane_sync = _metadata_sync(thread.id, thread, state="metadata")
    lane, _ = await ctx.registry.add_lane_with_sync(
        id=thread.id,
        handle=handle,
        source="attached",
        cwd=thread.cwd,
        status="idle",
        sync=lane_sync,
        audit_op=audit_op,
        audit_detail=handle,
    )
    await _record_observed_model(lane, thread, lane_sync, ctx)
    if sync:
        await _sync_lane(lane, ctx, full=False, metadata=thread)
    return lane


async def _read_thread_metadata(ctx: Ctx, thread_id: str) -> ThreadInfo:
    result = await ctx.client.thread_read(thread_id, include_turns=False)
    try:
        return ThreadResult.model_validate(result).thread
    except PydanticValidationError as exc:
        raise AppServerError(
            f"thread/read metadata for {thread_id!r} returned an invalid payload"
        ) from exc


async def _sync_lane(
    lane: Lane,
    ctx: Ctx,
    *,
    full: bool,
    metadata: ThreadInfo | None = None,
    limits: SyncLimits | None = None,
    max_turns: int = 50,
    max_items: int = 500,
    max_bytes: int = 524_288,
    max_seconds: float = 5.0,
) -> LaneSync:
    started = time_module.monotonic()
    try:
        async with asyncio.timeout(max_seconds):
            return await _sync_lane_body(
                lane,
                ctx,
                full=full,
                metadata=metadata,
                limits=limits,
                max_turns=max_turns,
                max_items=max_items,
                max_bytes=max_bytes,
                max_seconds=max_seconds,
                started=started,
            )
    except TimeoutError as exc:
        raise AppServerError(
            f"sync timed out after {max_seconds:g}s while refreshing {lane.id!r}"
        ) from exc


async def _sync_lane_body(
    lane: Lane,
    ctx: Ctx,
    *,
    full: bool,
    metadata: ThreadInfo | None,
    limits: SyncLimits | None,
    max_turns: int,
    max_items: int,
    max_bytes: int,
    max_seconds: float,
    started: float,
) -> LaneSync:
    thread = metadata or await _read_thread_metadata(ctx, lane.id)
    await observe_thread(ctx.registry, thread, relationship_source="thread/read")
    previous = await ctx.registry.get_lane_sync(lane.id)
    remaining_seconds = max(0.000_001, max_seconds - (time_module.monotonic() - started))
    history = await backfill_codex_history(
        client=ctx.client,
        registry=ctx.registry,
        lane=lane,
        cursor=previous.history_cursor if previous is not None else None,
        backwards_cursor=(previous.history_backwards_cursor if previous is not None else None),
        recent_cursor=previous.history_recent_cursor if previous is not None else None,
        pending_backwards_cursor=(
            previous.history_pending_backwards_cursor if previous is not None else None
        ),
        item_turn_id=previous.history_item_turn_id if previous is not None else None,
        item_turn_cursor=(previous.history_item_turn_cursor if previous is not None else None),
        item_turn_direction=(
            previous.history_item_turn_direction if previous is not None else None
        ),
        item_cursor=previous.history_item_cursor if previous is not None else None,
        cursor_guard=previous.history_cursor_guard if previous is not None else None,
        history_complete=previous.history_complete if previous is not None else False,
        history_capability=(previous.history_capability if previous is not None else "unknown"),
        max_turns=max_turns,
        max_items=max_items,
        max_seconds=remaining_seconds,
        max_bytes=max_bytes,
        capture=ctx.capture,
    )
    local_byte_budget = max(0, max_bytes - history.bytes_scanned)
    requested_limits = limits or SyncLimits()
    top_bytes = min(requested_limits.top_bytes, local_byte_budget // 2)
    local_limits = SyncLimits(
        top_bytes=top_bytes,
        tail_bytes=min(requested_limits.tail_bytes, local_byte_budget - top_bytes),
        tail_lines=requested_limits.tail_lines,
        full_bytes=min(requested_limits.full_bytes, local_byte_budget),
    )
    sync = await _sync_from_thread(
        lane.id,
        thread,
        full=full,
        previous=previous,
        limits=local_limits,
    )
    duration_ms = max(0, round((time_module.monotonic() - started) * 1000))
    sync = sync.model_copy(
        update={
            "history_source": history.source,
            "history_cursor": history.cursor,
            "history_backwards_cursor": (history.backwards_cursor),
            "history_recent_cursor": history.recent_cursor,
            "history_pending_backwards_cursor": history.pending_backwards_cursor,
            "history_item_turn_id": history.item_turn_id,
            "history_item_turn_cursor": history.item_turn_cursor,
            "history_item_turn_direction": history.item_turn_direction,
            "history_item_cursor": history.item_cursor,
            "history_cursor_guard": history.cursor_guard,
            "history_complete": history.complete,
            "history_capability": history.capability,
            "observation_enabled": history.observation_enabled,
            "pages_scanned": history.pages_scanned,
            "turns_indexed": history.turns_indexed,
            "items_indexed": history.items_indexed,
            "scanned_bytes": sync.scanned_bytes + history.bytes_scanned,
            "duration_ms": duration_ms,
            "truncated": sync.truncated or history.truncated,
            "transcript_partial": not history.complete,
        }
    )
    sync = await ctx.registry.upsert_lane_sync(sync)
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


async def _sync_from_thread(
    lane_id: str,
    thread: ThreadInfo,
    *,
    full: bool,
    previous: LaneSync | None = None,
    limits: SyncLimits | None = None,
) -> LaneSync:
    if thread.path is None:
        return _metadata_sync(lane_id, thread, state="metadata")
    previous_source = None
    if (
        previous is not None
        and previous.source_path == thread.path
        and previous.source_device is not None
        and previous.source_inode is not None
        and previous.source_size is not None
        and previous.source_mtime_ns is not None
    ):
        previous_source = SourceIdentity(
            path=previous.source_path,
            device=previous.source_device,
            inode=previous.source_inode,
            size=previous.source_size,
            mtime_ns=previous.source_mtime_ns,
        )
    facts = await asyncio.to_thread(
        scan_codex_jsonl,
        thread.path,
        full=full,
        limits=limits or SyncLimits(),
        previous=previous_source,
        previous_offset=previous.next_offset if previous is not None else None,
    )
    if facts.unchanged and previous is not None:
        return previous.model_copy(
            update={
                "display_name": thread.name or previous.display_name,
                "preview": _short(thread.preview, limit=200) or previous.preview,
                "cwd": thread.cwd or previous.cwd,
                "source": thread.source_kind or previous.source,
                "thread_source": thread.thread_source or previous.thread_source,
                "model_provider": thread.model_provider or previous.model_provider,
                "model": thread.model or previous.model,
                "reasoning_effort": thread.reasoning_effort or previous.reasoning_effort,
                "session_id": thread.session_id or previous.session_id,
                "pages_scanned": 0,
                "turns_indexed": 0,
                "items_indexed": 0,
                "unchanged_skipped": True,
                "scanned_bytes": 0,
                "duration_ms": 0,
            }
        )
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
        next_offset=facts.next_offset,
        error=facts.error,
        cwd=facts.cwd or (previous.cwd if previous is not None else None),
        source=facts.source_kind or (previous.source if previous is not None else None),
        thread_source=(
            facts.thread_source or (previous.thread_source if previous is not None else None)
        ),
        model_provider=(
            facts.model_provider or (previous.model_provider if previous is not None else None)
        ),
        model=facts.model or (previous.model if previous is not None else None),
        reasoning_effort=(
            facts.reasoning_effort or (previous.reasoning_effort if previous is not None else None)
        ),
        session_id=facts.session_id or (previous.session_id if previous is not None else None),
        latest_event_at=(
            facts.latest_event_at or (previous.latest_event_at if previous is not None else None)
        ),
        latest_turn_id=(
            facts.latest_turn_id or (previous.latest_turn_id if previous is not None else None)
        ),
        transcript_partial=state != "complete",
        unchanged_skipped=False,
        scanned_bytes=facts.bytes_scanned,
        truncated=(
            state != "complete"
            and facts.source is not None
            and facts.bytes_scanned < facts.source.size
        ),
        history_source=previous.history_source if previous is not None else None,
        history_cursor=previous.history_cursor if previous is not None else None,
        history_backwards_cursor=(
            previous.history_backwards_cursor if previous is not None else None
        ),
        history_recent_cursor=(previous.history_recent_cursor if previous is not None else None),
        history_pending_backwards_cursor=(
            previous.history_pending_backwards_cursor if previous is not None else None
        ),
        history_item_turn_id=(previous.history_item_turn_id if previous is not None else None),
        history_item_turn_cursor=(
            previous.history_item_turn_cursor if previous is not None else None
        ),
        history_item_turn_direction=(
            previous.history_item_turn_direction if previous is not None else None
        ),
        history_item_cursor=(previous.history_item_cursor if previous is not None else None),
        history_cursor_guard=(previous.history_cursor_guard if previous is not None else None),
        history_complete=previous.history_complete if previous is not None else False,
        history_capability=(previous.history_capability if previous is not None else "unknown"),
        observation_enabled=(previous.observation_enabled if previous is not None else False),
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
    next_offset: int | None = None,
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
    history_source: str | None = None,
    history_cursor: str | None = None,
    history_backwards_cursor: str | None = None,
    history_recent_cursor: str | None = None,
    history_pending_backwards_cursor: str | None = None,
    history_item_turn_id: str | None = None,
    history_item_turn_cursor: str | None = None,
    history_item_turn_direction: Literal["asc", "desc"] | None = None,
    history_item_cursor: str | None = None,
    history_cursor_guard: str | None = None,
    history_complete: bool = False,
    history_capability: HistoryCapability = "unknown",
    observation_enabled: bool = False,
    pages_scanned: int = 0,
    turns_indexed: int = 0,
    items_indexed: int = 0,
    unchanged_skipped: bool = False,
    scanned_bytes: int = 0,
    duration_ms: int = 0,
    truncated: bool = False,
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
        next_offset=next_offset,
        error=error,
        display_name=thread.name,
        preview=_short(thread.preview, limit=200),
        cwd=cwd or thread.cwd,
        source=source or thread.source_kind,
        thread_source=thread_source or thread.thread_source,
        model_provider=model_provider or thread.model_provider,
        model=model or thread.model,
        reasoning_effort=reasoning_effort or thread.reasoning_effort,
        session_id=session_id or thread.session_id,
        latest_event_at=latest_event_at,
        latest_turn_id=latest_turn_id,
        transcript_partial=transcript_partial,
        history_source=history_source,
        history_cursor=history_cursor,
        history_backwards_cursor=history_backwards_cursor,
        history_recent_cursor=history_recent_cursor,
        history_pending_backwards_cursor=history_pending_backwards_cursor,
        history_item_turn_id=history_item_turn_id,
        history_item_turn_cursor=history_item_turn_cursor,
        history_item_turn_direction=history_item_turn_direction,
        history_item_cursor=history_item_cursor,
        history_cursor_guard=history_cursor_guard,
        history_complete=history_complete,
        history_capability=history_capability,
        observation_enabled=observation_enabled,
        pages_scanned=pages_scanned,
        turns_indexed=turns_indexed,
        items_indexed=items_indexed,
        unchanged_skipped=unchanged_skipped,
        scanned_bytes=scanned_bytes,
        duration_ms=duration_ms,
        truncated=truncated,
    )


async def _record_direct_send_receipt(
    ctx: Ctx, lane: Lane, *, status: str, error: str | None = None
) -> None:
    now = ctx.registry.now_iso()
    await ctx.registry.upsert_message_receipt(
        MessageReceipt(
            lane=lane.id,
            provider="codex",
            provider_thread_id=lane.id,
            status=status,  # type: ignore[arg-type]
            error=error,
            created_at=now,
            sent_at=now if status == "sent" else None,
            failed_at=now if status == "failed" else None,
            updated_at=now,
        )
    )


def _bounded_error(exc: BaseException, ctx: Ctx) -> str:
    bounded = bound_text(str(exc), ctx.capture)
    return bounded.text if bounded is not None else ""


async def _record_queue_receipt(
    ctx: Ctx, lane: Lane, queued_message_id: int, *, status: str, error: str | None = None
) -> None:
    now = ctx.registry.now_iso()
    await ctx.registry.upsert_message_receipt(
        MessageReceipt(
            lane=lane.id,
            queued_message_id=queued_message_id,
            provider="codex",
            provider_thread_id=lane.id,
            dispatch_message_id=f"queue:{queued_message_id}",
            status=status,  # type: ignore[arg-type]
            error=error,
            created_at=now,
            sent_at=now if status == "sent" else None,
            failed_at=now if status == "failed" else None,
            updated_at=now,
        )
    )


async def send(inp: LaneTextInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve_message_target(ctx, inp.lane)
    _require_writable(lane, ctx)
    try:
        await _prepare_attached_write(lane, ctx)
        turn_settings = await load_turn_start_settings(ctx.registry, lane.id)
        await ctx.registry.update_lane_status(lane.id, "busy")
        await ctx.client.turn_start(
            lane.id,
            inp.text,
            cwd=lane.cwd or ".",
            permission_profile=turn_settings.permission_profile,
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
        error = _bounded_error(exc, ctx)
        await ctx.registry.record_turn_request_failed(lane.id, error)
        await _record_direct_send_receipt(ctx, lane, status="failed", error=error)
        await ctx.registry.log_action(
            "send", lane=lane.id, detail=inp.text[:120], outcome=project_error(exc).code
        )
        raise
    await _record_direct_send_receipt(ctx, lane, status="sent")
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
            lane = await _resolve_message_target(ctx, inp.lane)
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
                    permission_profile=turn_settings.permission_profile,
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
                error = _bounded_error(exc, ctx)
                await ctx.registry.record_turn_request_failed(lane.id, error)
                await _record_direct_send_receipt(ctx, lane, status="failed", error=error)
                await ctx.registry.log_action(
                    "send", lane=lane.id, detail=text[:120], outcome=project_error(exc).code
                )
                raise
            await _record_direct_send_receipt(ctx, lane, status="sent")
            await ctx.registry.log_action("send", lane=lane.id, detail=text[:120])
            return ActionAck(**_managed_identity(lane, ctx), op="interject")
        case "queue":
            lane = await _resolve_message_target(ctx, inp.lane)
            _require_writable(lane, ctx)
            message = await ctx.registry.enqueue_message(lane=lane.id, text=text)
            await _record_queue_receipt(ctx, lane, message.id, status="created")
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
    lane = await _resolve_message_target(ctx, inp.lane)
    _require_writable(lane, ctx)
    turn_id = _require_active_turn(lane, "steer")
    await _prepare_attached_write(lane, ctx)
    await ctx.client.turn_steer(lane.id, turn_id, inp.text)
    await ctx.registry.log_action("steer", lane=lane.id, detail=inp.text[:120])
    return ActionAck(**_managed_identity(lane, ctx), op="steer")


async def brief(inp: LaneTextInput, ctx: Ctx) -> ActionAck:
    lane = await _resolve_message_target(ctx, inp.lane)
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


async def _inbox_message_view(message: InboxMessage, ctx: Ctx) -> InboxMessageView:
    recipient = await ctx.registry.find_lane(message.recipient_lane)
    source = await ctx.registry.find_lane(message.source_lane) if message.source_lane else None
    return InboxMessageView(
        id=message.id,
        recipient_ref=recipient.ref if recipient is not None else None,
        recipient_lane=message.recipient_lane,
        source_ref=source.ref if source is not None else None,
        source_lane=message.source_lane,
        subscription_id=message.subscription_id,
        kind=message.kind,
        subject=message.subject,
        body=message.body,
        payload=message.payload,
        state=message.state,
        delivery=message.delivery,
        queued_message_id=message.queued_message_id,
        created_at=message.created_at.isoformat(),
        delivered_at=message.delivered_at.isoformat() if message.delivered_at else None,
        acked_at=message.acked_at.isoformat() if message.acked_at else None,
    )


async def _subscription_view(subscription: Subscription, ctx: Ctx) -> SubscriptionView:
    target = await ctx.registry.find_lane(subscription.target_lane)
    subscriber = await ctx.registry.find_lane(subscription.subscriber_lane)
    return SubscriptionView(
        id=subscription.id,
        target_ref=target.ref if target is not None else subscription.target_lane,
        target_lane=subscription.target_lane,
        subscriber_ref=subscriber.ref if subscriber is not None else subscription.subscriber_lane,
        subscriber_lane=subscription.subscriber_lane,
        when=subscription.when,
        delivery=subscription.delivery,
        deliver=subscription.deliver,
        tail=subscription.tail,
        once=subscription.once,
        ack=subscription.ack,
        attribution=subscription.attribution,
        state=subscription.state,
        created_at=subscription.created_at.isoformat(),
        updated_at=subscription.updated_at.isoformat(),
        last_matched_at=subscription.last_matched_at.isoformat()
        if subscription.last_matched_at
        else None,
        last_inbox_message_id=subscription.last_inbox_message_id,
    )


async def inbox_list(inp: InboxListInput, ctx: Ctx) -> InboxList:
    lane = None
    if inp.lane is not None:
        lane = await _resolve(ctx, inp.lane)
    elif inp.caller_thread_id is not None:
        lane = await _resolve_self(ctx, inp.caller_thread_id)
    messages = await ctx.registry.list_inbox_messages(
        lane=lane.id if lane is not None else None,
        state=inp.state,
        kind=inp.kind,
        limit=inp.limit,
    )
    return InboxList(messages=[await _inbox_message_view(message, ctx) for message in messages])


async def inbox_read(inp: InboxReadInput, ctx: Ctx) -> InboxMessageView:
    return await _inbox_message_view(await ctx.registry.get_inbox_message(inp.id), ctx)


async def inbox_ack(inp: InboxAckInput, ctx: Ctx) -> InboxAckResult:
    if inp.id is not None and (inp.all or inp.lane is not None):
        raise ValidationError("ack one message by id or use --all with an optional lane")
    if inp.id is not None:
        message = await ctx.registry.ack_inbox_message(inp.id)
        return InboxAckResult(acked=1, message=await _inbox_message_view(message, ctx))
    if not inp.all:
        raise ValidationError("inbox ack requires a message id or --all")
    lane = (
        await _resolve(ctx, inp.lane)
        if inp.lane
        else await _resolve_self(ctx, inp.caller_thread_id)
    )
    return InboxAckResult(acked=await ctx.registry.ack_inbox_messages_for_lane(lane.id))


async def _server_request_view(request: ServerRequest, ctx: Ctx) -> ServerRequestView:
    if request.id is None:
        raise RuntimeError("persisted interactive request has no local id")
    lane = await ctx.registry.find_lane(request.lane) if request.lane is not None else None
    return ServerRequestView(
        id=request.id,
        lane_ref=lane.ref if lane is not None else None,
        lane=request.lane,
        method=request.method,
        category=request.category,
        state=request.state,
        received_at=request.received_at,
        deadline_at=request.deadline_at,
        resolved_at=request.resolved_at,
        response_summary=request.response_summary,
        error=request.error,
        expected_response=expected_response(request.method),
    )


async def server_request_list(inp: ServerRequestListInput, ctx: Ctx) -> ServerRequestList:
    lane = await _resolve(ctx, inp.lane) if inp.lane is not None else None
    requests = await ctx.registry.list_server_requests(
        state=inp.state,
        lane=lane.id if lane is not None else None,
        limit=inp.limit,
    )
    return ServerRequestList(
        requests=[await _server_request_view(request, ctx) for request in requests]
    )


async def server_request_respond(
    inp: ServerRequestRespondInput, ctx: Ctx
) -> ServerRequestRespondResult:
    request = await respond_to_server_request(ctx, inp.id, inp.response)
    return ServerRequestRespondResult(request=await _server_request_view(request, ctx))


async def subscribe(inp: SubscribeInput, ctx: Ctx) -> SubscriptionView:
    target = await _resolve(ctx, inp.target)
    settings = _subscription_settings(inp)
    subscriber = (
        await _resolve_self(ctx, inp.caller_thread_id)
        if settings["to"] == "self"
        else await _resolve(ctx, settings["to"])
    )
    if (
        settings["delivery"] == "turn"
        and not _can_write(subscriber, ctx)
        and not _subscription_delivery_explicit(inp)
    ):
        settings["delivery"] = "inbox"
    if settings["delivery"] == "turn" and not _can_write(subscriber, ctx):
        raise AuthorityError(
            "turn delivery requires a writable subscriber lane; use delivery:inbox or enable "
            "policy.allow_attached_writes for attached-lane turn delivery"
        )
    now = datetime.fromisoformat(ctx.registry.now_iso())
    subscription = Subscription(
        id=f"sub_{uuid.uuid4().hex[:8]}",
        target_lane=target.id,
        subscriber_lane=subscriber.id,
        when=settings["when"],
        delivery=settings["delivery"],
        deliver=settings["deliver"],
        tail=settings["tail"],
        once=settings["once"],
        ack=settings["ack"],
        attribution=settings["attribution"],
        created_at=now,
        updated_at=now,
    )
    saved = await ctx.registry.add_subscription(subscription)
    await ctx.registry.log_action(
        "subscribe",
        lane=target.id,
        detail=f"{saved.when}->{subscriber.ref}:{saved.delivery}",
    )
    return await _subscription_view(saved, ctx)


async def subscription_list(inp: SubscriptionListInput, ctx: Ctx) -> SubscriptionList:
    target = await _resolve(ctx, inp.target) if inp.target is not None else None
    subscriber = await _resolve(ctx, inp.subscriber) if inp.subscriber is not None else None
    subscriptions = await ctx.registry.list_subscriptions(
        target_lane=target.id if target is not None else None,
        subscriber_lane=subscriber.id if subscriber is not None else None,
        state=inp.state,
    )
    return SubscriptionList(
        subscriptions=[
            await _subscription_view(subscription, ctx) for subscription in subscriptions
        ]
    )


async def unsubscribe(inp: SubscriptionIdInput, ctx: Ctx) -> SubscriptionRemoved:
    return SubscriptionRemoved(id=inp.id, removed=await ctx.registry.remove_subscription(inp.id))


def _subscription_settings(inp: SubscribeInput) -> _SubscriptionSettings:
    settings: _SubscriptionSettings = {
        "when": "done",
        "to": "self",
        "delivery": "turn",
        "deliver": "idle",
        "tail": 1,
        "once": True,
        "ack": "auto",
        "attribution": True,
    }
    spec = inp.spec
    if spec and spec not in {"true", "default", "all"}:
        for part in [chunk.strip() for chunk in spec.split(",") if chunk.strip()]:
            if ":" in part:
                key, value = part.split(":", 1)
            elif "=" in part:
                key, value = part.split("=", 1)
            else:
                raise ValidationError(f"subscription spec entry {part!r} must use key:value")
            key = key.strip()
            if key not in settings:
                raise ValidationError(f"unknown subscription spec key {key!r}")
            _set_subscription_setting(settings, key, _parse_subscription_value(key, value.strip()))
    if inp.when is not None:
        settings["when"] = inp.when
    if inp.to is not None:
        settings["to"] = inp.to
    if inp.delivery is not None:
        settings["delivery"] = inp.delivery
    if inp.deliver is not None:
        settings["deliver"] = inp.deliver
    if inp.tail is not None:
        settings["tail"] = inp.tail
    if inp.once is not None:
        settings["once"] = inp.once
    if inp.ack is not None:
        settings["ack"] = inp.ack
    if inp.attribution is not None:
        settings["attribution"] = inp.attribution
    return settings


def _subscription_delivery_explicit(inp: SubscribeInput) -> bool:
    if inp.delivery is not None:
        return True
    spec = inp.spec or ""
    if spec in {"", "true", "default", "all"}:
        return False
    return any(
        part.strip().startswith(("delivery:", "delivery="))
        for part in spec.split(",")
        if part.strip()
    )


def _set_subscription_setting(settings: _SubscriptionSettings, key: str, value: object) -> None:
    match key:
        case "when":
            settings["when"] = cast(SubscriptionWhen, value)
        case "to":
            settings["to"] = cast(str, value)
        case "delivery":
            settings["delivery"] = cast(SubscriptionDelivery, value)
        case "deliver":
            settings["deliver"] = cast(SubscriptionDeliverPolicy, value)
        case "tail":
            settings["tail"] = cast(int, value)
        case "once":
            settings["once"] = cast(bool, value)
        case "ack":
            settings["ack"] = cast(SubscriptionAckPolicy, value)
        case "attribution":
            settings["attribution"] = cast(bool, value)


def _parse_subscription_value(key: str, value: str) -> object:
    if key == "when":
        allowed = {"done", "completed", "failed", "needs-attention", "approval", "idle", "activity"}
        if value not in allowed:
            raise ValidationError(f"subscription when must be one of {', '.join(sorted(allowed))}")
        return value
    if key == "delivery":
        allowed = {"turn", "inbox"}
        if value not in allowed:
            raise ValidationError("subscription delivery must be turn or inbox")
        return value
    if key == "deliver":
        allowed = {"idle", "now"}
        if value not in allowed:
            raise ValidationError("subscription deliver must be idle or now")
        return value
    if key == "ack":
        allowed = {"auto", "manual"}
        if value not in allowed:
            raise ValidationError("subscription ack must be auto or manual")
        return value
    if key == "tail":
        try:
            tail = int(value)
        except ValueError as exc:
            raise ValidationError("subscription tail must be an integer") from exc
        if tail < 0:
            raise ValidationError("subscription tail must be >= 0")
        return tail
    if key in {"once", "attribution"}:
        normalized = value.lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValidationError(f"subscription {key} must be true or false")
    return value


async def show(inp: ShowInput, ctx: Ctx) -> LaneDetail:
    resolved = await resolve_managed_selector(ctx, inp.lane, allow_fuzzy=True)
    if resolved.lane is None:
        raise NotFoundError(f"no managed thread {inp.lane!r}")
    lane = resolved.lane
    sync = await ctx.registry.get_lane_sync(lane.id)
    model_settings = await ctx.registry.get_lane_model_settings(lane.id)
    transcript: list[TranscriptItem] = []
    if inp.topology:
        thread = await _read_thread_metadata(ctx, lane.id)
        await observe_thread(ctx.registry, thread, relationship_source="thread/read")
        descendants = await ctx.client.thread_list(
            limit=inp.topology_limit,
            ancestor_thread_id=lane.id,
            archived=False,
            sort_direction="desc",
            sort_key="updated_at",
            use_state_db_only=True,
        )
        await observe_threads(
            ctx.registry,
            descendants,
            lifecycle_state="active",
            relationship_source="thread/list:ancestor",
        )
    if inp.include_transcript:
        result = await ctx.client.thread_read(lane.id, include_turns=True)
        await index_codex_thread_read(ctx.registry, lane, result, ctx.capture)
        transcript = _transcript_from_thread(result, limit=inp.max_items)
    topology = await topology_views(ctx.registry, [lane.id], max_nodes=inp.topology_limit)
    return LaneDetail(
        **_ref(lane, ctx).model_dump(),
        active_turn_id=lane.active_turn_id,
        latest_turn=_latest_turn_view(lane),
        sync=_sync_view(sync),
        model=_model_view(model_settings, sync),
        transcript=transcript,
        topology=topology[lane.id],
    )


async def sync_lane(inp: LaneSyncInput, ctx: Ctx) -> LaneSyncResult:
    resolved = await resolve_thread_selector(ctx, inp.lane, allow_unmanaged_raw=True)
    if resolved.lane is None:
        thread = await _read_thread_metadata(ctx, resolved.thread_id)
        lane = await _register_attached_thread(thread, ctx, sync=False, audit_op="attach")
    else:
        lane = resolved.lane
    jsonl_budget = max(1, inp.max_bytes // 2)
    try:
        async with asyncio.timeout(inp.max_seconds):
            sync = await _sync_lane(
                lane,
                ctx,
                full=inp.full,
                limits=SyncLimits(
                    top_bytes=jsonl_budget,
                    tail_bytes=jsonl_budget,
                    full_bytes=inp.max_bytes,
                ),
                max_turns=inp.max_turns,
                max_items=inp.max_items,
                max_bytes=inp.max_bytes,
                max_seconds=inp.max_seconds,
            )
            lane = await _reconcile_archive_membership(lane, ctx)
    except TimeoutError as exc:
        raise AppServerError(
            f"sync timed out after {inp.max_seconds:g}s while refreshing {lane.id!r}"
        ) from exc
    model_settings = await ctx.registry.get_lane_model_settings(lane.id)
    await ctx.registry.log_action(
        "sync", lane=lane.id, detail=f"state={sync.state}; full={inp.full}"
    )
    return LaneSyncResult(
        **_managed_identity(lane, ctx),
        sync=_sync_view(sync),
        model=_model_view(model_settings, sync),
    )


async def _reconcile_archive_membership(lane: Lane, ctx: Ctx) -> Lane:
    if await _thread_list_contains(ctx, lane.id, archived=True):
        await ctx.registry.mark_provider_thread_state("codex", lane.id, "archived")
        if lane.status != "archived":
            await ctx.registry.update_lane_status(lane.id, "archived")
            return await ctx.registry.get_lane(lane.id)
        return lane
    if lane.status == "archived" and await _thread_list_contains(ctx, lane.id, archived=False):
        await ctx.registry.mark_provider_thread_state("codex", lane.id, "active")
        await ctx.registry.mark_lane_idle(lane.id)
        return await ctx.registry.get_lane(lane.id)
    return lane


async def _thread_list_contains(ctx: Ctx, thread_id: str, *, archived: bool) -> bool:
    threads = await ctx.client.thread_list(
        limit=100,
        archived=archived,
        search_term=thread_id,
        sort_direction="desc",
        sort_key="updated_at",
        use_state_db_only=True,
    )
    return any(thread.id == thread_id for thread in threads)


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
    await index_codex_thread_read(ctx.registry, lane, result, ctx.capture)
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
            summary = await _history_summary_from_index(lane, ctx)
            if _history_summary_matches(summary, inp):
                summaries.append(summary)
            if len(summaries) >= inp.limit:
                break
        return HistoryOutput(mode="overview", threads=summaries)

    if inp.lane is None:
        raise ValidationError("history view requires a thread selector")
    lane = await _resolve(ctx, inp.lane)
    result = await ctx.client.thread_read(lane.id, include_turns=True)
    summary, items, tools, files = await _history_details(lane, result, ctx)
    if mode == "summary":
        return HistoryOutput(mode="summary", thread=summary, tools=tools, files=files)
    if mode == "tools":
        return HistoryOutput(mode="tools", thread=summary, tools=tools[: inp.limit])
    if mode == "files":
        return HistoryOutput(mode="files", thread=summary, files=files[: inp.limit])
    return HistoryOutput(
        mode="items",
        thread=summary,
        items=(
            history_items_from_thread(
                result,
                item_type=inp.item_type,
                role=inp.role,
                phase=inp.phase,
                tool=inp.tool,
                tool_server=inp.tool_server,
                tool_status=inp.tool_status,
                errored=inp.errored,
                mentions_thread=inp.mentions_thread,
                arg_key=inp.arg_key,
                grep=inp.grep,
                raw=True,
                limit=inp.limit,
            )
            if inp.raw
            else history_items_from_indexed(
                items.indexed,
                items.refs,
                item_type=inp.item_type,
                role=inp.role,
                phase=inp.phase,
                tool=inp.tool,
                tool_server=inp.tool_server,
                tool_status=inp.tool_status,
                errored=inp.errored,
                mentions_thread=inp.mentions_thread,
                arg_key=inp.arg_key,
                grep=inp.grep,
                raw=False,
                limit=inp.limit,
            )
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


async def _history_summary_from_index(lane: Lane, ctx: Ctx) -> HistoryThreadSummary:
    stats = await ctx.registry.get_thread_history_summary_stats(lane=lane.id)
    worktree = await detect_worktree(lane.cwd)
    transcript_bytes = stats.transcript_bytes
    return HistoryThreadSummary(
        ref=lane.ref,
        id=lane.id,
        handle=lane.handle,
        source=lane.source,
        status=lane.status,
        cwd=lane.cwd,
        first_event_at=stats.first_event_at,
        last_event_at=stats.last_event_at,
        turns=stats.turns,
        items=stats.items,
        messages=stats.messages,
        tool_calls=stats.tool_calls,
        unique_tools=sorted(tool.tool for tool in stats.tools),
        files_changed_count=stats.files_changed_count,
        files_changed=[HistoryFileStat(path=file.path, count=file.count) for file in stats.files],
        transcript_bytes=transcript_bytes,
        estimated_tokens=(transcript_bytes // 4) if transcript_bytes is not None else None,
        subagents_count=len(stats.child_thread_ids),
        subagent_thread_ids=stats.child_thread_ids,
        worktree=worktree,
    )


async def _history_details(
    lane: Lane, result: dict[str, object], ctx: Ctx
) -> tuple[HistoryThreadSummary, _IndexedHistory, list[HistoryToolStat], list[HistoryFileStat]]:
    await index_codex_thread_read(ctx.registry, lane, result, ctx.capture)
    summary = await _history_summary_from_index(lane, ctx)
    indexed_items = await ctx.registry.list_thread_items(lane=lane.id, limit=None)
    refs = await ctx.registry.list_thread_item_refs_many(indexed_items)
    tools, files = history_rollups_from_indexed(indexed_items, refs)
    return summary, _IndexedHistory(indexed=indexed_items, refs=refs), tools, files


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


async def query(inp: QueryInput, ctx: Ctx) -> QueryOutput:
    if inp.limit > inp.max_scan:
        raise ValidationError("query limit cannot exceed max_scan")
    if inp.query is not None and not inp.query.strip():
        raise ValidationError("query text cannot be empty")
    if inp.query is None and not _query_has_structural_filter(inp):
        raise ValidationError("query requires text or at least one structural filter")

    lane_map = {lane.id: lane for lane in await ctx.registry.list_lanes(include_archived=True)}
    root_filters = _search_roots(inp)
    since = _parse_bound(inp.since, start=True)
    until = _parse_bound(inp.until, start=False)
    eligible = [
        lane
        for lane in lane_map.values()
        if _lane_matches_archived_filter(lane, archived=inp.archived)
        and (inp.source is None or lane.source == inp.source)
        and (inp.status is None or lane.status == inp.status)
        and not _outside_roots(lane.cwd, root_filters)
        and _lane_inside_date(lane, inp.date_field, since=since, until=until)
    ]
    if inp.lane is not None:
        resolved = await resolve_thread_selector(
            ctx, inp.lane, allow_unmanaged_raw=False, allow_fuzzy=True
        )
        eligible = [lane for lane in eligible if lane.id == resolved.thread_id]

    items, scanned = await ctx.registry.query_thread_items(
        query=inp.query,
        lanes={lane.id for lane in eligible},
        item_type=inp.item_type,
        role=inp.role,
        tool=inp.tool,
        tool_server=inp.tool_server,
        tool_status=inp.tool_status,
        errored=inp.errored,
        file=inp.file,
        file_under=inp.file_under,
        ext=inp.ext,
        mentions_thread=inp.mentions_thread,
        turn_id=inp.turn,
        item_id=inp.item_id,
        arg_key=inp.arg_key,
        raw_retained=inp.raw_retained,
        limit=inp.limit,
        max_scan=inp.max_scan,
    )
    refs_by_item = await ctx.registry.list_thread_item_refs_many(items)
    matches: list[QueryMatch] = []
    for item in items:
        if item.lane is None:
            continue
        lane = lane_map.get(item.lane)
        if lane is None:
            continue
        refs = refs_by_item.get((item.provider, item.provider_thread_id, item.item_id), [])
        matches.append(_query_match(lane, item, refs, query=inp.query))
    return QueryOutput(query=inp.query, matches=matches, scanned=scanned)


def _query_has_structural_filter(inp: QueryInput) -> bool:
    return (
        any(
            value is not None
            for value in (
                inp.lane,
                inp.directory,
                inp.repo,
                inp.source,
                inp.status,
                inp.since,
                inp.until,
                inp.item_type,
                inp.role,
                inp.tool,
                inp.tool_server,
                inp.tool_status,
                inp.errored,
                inp.file,
                inp.file_under,
                inp.ext,
                inp.mentions_thread,
                inp.turn,
                inp.item_id,
                inp.arg_key,
                inp.raw_retained,
            )
        )
        or inp.archived
    )


def _query_match(
    lane: Lane, item: ThreadItem, refs: list[ThreadItemRef], *, query: str | None
) -> QueryMatch:
    raw = item.payload or {}
    ref_views = [QueryRef(type=ref.ref_type, value=ref.ref_value) for ref in refs]
    file_refs = [ref.ref_value for ref in refs if ref.ref_type == "file"]
    return QueryMatch(
        ref=lane.ref,
        id=lane.id,
        handle=lane.handle,
        source=lane.source,
        status=lane.status,
        cwd=lane.cwd,
        item_id=item.item_id,
        turn_id=item.turn_id,
        type=item.item_type,
        role=item.role,
        tool=item.tool,
        phase=item.phase,
        command=item.command,
        command_cwd=item.cwd,
        arguments=item.arguments,
        success=item.success,
        error=item.error,
        agent_nickname=item.agent_nickname,
        agent_role=item.agent_role,
        snippet=_query_snippet(item, refs, query=query),
        files=file_refs,
        refs=ref_views,
        created_at=item.created_at,
        inserted_at=item.inserted_at,
        raw_retained=item.raw_retained,
        tool_server=item.server
        or _string_from_raw(raw, "server")
        or _ref_value(refs, "tool_server"),
        tool_status=item.status
        or _string_from_raw(raw, "status")
        or _ref_value(refs, "tool_status"),
        errored=item.error is not None
        or item.success is False
        or bool(raw.get("error"))
        or any(ref.ref_type == "tool_error" for ref in refs),
        duration_ms=(
            item.duration_ms if item.duration_ms is not None else _int_from_raw(raw, "durationMs")
        ),
    )


def _query_snippet(item: ThreadItem, refs: list[ThreadItemRef], *, query: str | None) -> str:
    if item.text:
        return (
            _local_search_snippet(item.text, query)
            if query is not None
            else (_short(item.text) or "")
        )
    pieces = [item.tool or item.item_type]
    status = _ref_value(refs, "tool_status")
    server = _ref_value(refs, "tool_server")
    if status:
        pieces.append(status)
    if server:
        pieces.append(f"on {server}")
    files = [ref.ref_value for ref in refs if ref.ref_type == "file"]
    if files:
        pieces.append(f"files: {', '.join(files[:3])}")
    return " ".join(piece for piece in pieces if piece)


def _ref_value(refs: list[ThreadItemRef], ref_type: str) -> str | None:
    for ref in refs:
        if ref.ref_type == ref_type:
            return ref.ref_value
    return None


def _string_from_raw(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _int_from_raw(raw: dict[str, object], key: str) -> int | None:
    value = raw.get(key)
    return value if isinstance(value, int) else None


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


def _search_roots(inp: SearchInput | QueryInput) -> tuple[Path, ...]:
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


def _lane_inside_date(
    lane: Lane,
    date_field: str,
    *,
    since: float | None,
    until: float | None,
) -> bool:
    value = lane.created_at if date_field == "created_at" else lane.updated_at
    stamp = value.timestamp()
    if since is not None and stamp < since:
        return False
    return not (until is not None and stamp > until)


def _lane_matches_archived_filter(lane: Lane, *, archived: bool) -> bool:
    is_archived = lane.status == "archived"
    return is_archived if archived else not is_archived


def _local_search_snippet(text: str, query: str, *, radius: int = 80) -> str:
    folded = text.casefold()
    start = folded.find(query.casefold())
    if start < 0:
        return _short(text, limit=radius * 2) or ""
    left = max(0, start - radius)
    right = min(len(text), start + len(query) + radius)
    snippet = text[left:right].strip()
    if left > 0:
        snippet = "..." + snippet
    if right < len(text):
        snippet += "..."
    return snippet


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
    fork_cwd = inp.cwd or source.cwd or "."
    permission_profile = await resolve_permission_profile(
        ctx, inp.permission_profile, cwd=str(Path(fork_cwd).expanduser().resolve())
    )
    thread = await ctx.client.thread_fork(
        source.id,
        cwd=inp.cwd or source.cwd,
        permission_profile=permission_profile,
        sandbox=inp.sandbox,
        approval_policy=inp.approval_policy,
        approvals_reviewer=inp.approvals_reviewer,
        base_instructions=inp.base_instructions,
        developer_instructions=inp.developer_instructions,
        service_tier=explicit_service_tier,
        model=inp.model,
        model_provider=inp.model_provider,
        last_turn_id=inp.last_turn_id,
        ephemeral=inp.ephemeral,
    )
    await observe_thread(
        ctx.registry, thread, lifecycle_state="active", relationship_source="thread/fork"
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
            permission_profile=permission_profile,
            sandbox=inp.sandbox,
            approval_policy=inp.approval_policy,
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
    selected_ids: set[str] | None = None
    selector = inp.parent or inp.ancestor or inp.root
    if selector is not None:
        resolved = await resolve_thread_selector(
            ctx, selector, allow_unmanaged_raw=True, allow_fuzzy=False
        )
        parent_thread_id = resolved.thread_id if inp.parent is not None else None
        ancestor_thread_id = resolved.thread_id if inp.parent is None else None
        active = await ctx.client.thread_list(
            limit=inp.topology_limit,
            archived=False,
            sort_direction="desc",
            sort_key="updated_at",
            use_state_db_only=True,
            parent_thread_id=parent_thread_id,
            ancestor_thread_id=ancestor_thread_id,
        )
        related = active
        if inp.include_archived:
            related += await ctx.client.thread_list(
                limit=inp.topology_limit,
                archived=True,
                sort_direction="desc",
                sort_key="updated_at",
                use_state_db_only=True,
                parent_thread_id=parent_thread_id,
                ancestor_thread_id=ancestor_thread_id,
            )
        await observe_threads(ctx.registry, related, relationship_source="thread/list:topology")
        selected_ids = {thread.id for thread in related}
        if inp.root is not None:
            selected_ids.add(resolved.thread_id)
        lanes = [lane for lane in lanes if lane.id in selected_ids]
    syncs = await ctx.registry.get_lane_sync_many([lane.id for lane in lanes])
    models = await ctx.registry.get_lane_model_settings_many([lane.id for lane in lanes])
    topology = await topology_views(
        ctx.registry, [lane.id for lane in lanes], max_nodes=inp.topology_limit
    )
    return Roster(
        lanes=[
            _list_item(
                lane,
                syncs.get(lane.id),
                models.get(lane.id),
                ctx,
                topology.get(lane.id),
            )
            for lane in lanes
        ]
    )


def _short(text: str | None, limit: int = _PREVIEW_MAX) -> str | None:
    if text is None:
        return None
    collapsed = " ".join(text.split())  # flatten whitespace/newlines for a one-line preview
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _session(
    thread: ThreadInfo,
    *,
    archived: bool = False,
    topology: ThreadTopologyView | None = None,
) -> DiscoveredSession:
    return DiscoveredSession(
        id=thread.id,
        name=thread.name,
        preview=_short(thread.preview),
        cwd=thread.cwd,
        status=thread.status.type if thread.status is not None else None,
        source=thread.source_kind,
        archived=archived,
        ephemeral=thread.ephemeral,
        model=_model_view_from_thread(thread),
        topology=topology or ThreadTopologyView(),
    )


async def discover(inp: DiscoverInput, ctx: Ctx) -> Discovery:
    """List persisted Codex sessions (``thread/list``, state-db only) — read-only and
    distinct from ``roster``: these are candidates to ``attach``, not managed lanes.
    Discovery does not resume or register anything."""
    selector = inp.parent or inp.ancestor or inp.root
    parent_thread_id: str | None = None
    ancestor_thread_id: str | None = None
    if selector is not None:
        resolved = await resolve_thread_selector(
            ctx, selector, allow_unmanaged_raw=True, allow_fuzzy=False
        )
        if inp.parent is not None:
            parent_thread_id = resolved.thread_id
        else:
            ancestor_thread_id = resolved.thread_id
    threads = await ctx.client.thread_list(
        limit=inp.limit,
        archived=inp.archived,
        sort_direction="desc",
        sort_key="updated_at",
        use_state_db_only=True,
        parent_thread_id=parent_thread_id,
        ancestor_thread_id=ancestor_thread_id,
    )
    await observe_threads(
        ctx.registry,
        threads,
        lifecycle_state="archived" if inp.archived else "active",
        relationship_source="thread/list:discover",
    )
    managed = {lane.id for lane in await ctx.registry.list_lanes(include_archived=True)}
    threads = [thread for thread in threads if thread.id not in managed]
    topology = await topology_views(
        ctx.registry, [thread.id for thread in threads], max_nodes=inp.topology_limit
    )
    return Discovery(
        sessions=[
            _session(
                thread,
                archived=inp.archived,
                topology=topology.get(thread.id),
            )
            for thread in threads
        ]
    )


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


async def permission_profiles(inp: PermissionProfilesInput, ctx: Ctx) -> PermissionProfilesOutput:
    cwd = str(Path(inp.cwd).expanduser().resolve())
    source: Literal["app-server", "registry"] = "registry"
    state: Literal["ready", "empty", "unsupported"]
    hint: str | None = None
    if inp.refresh:
        try:
            snapshot = await refresh_permission_profiles(ctx, cwd=cwd)
        except ClientAppServerError as exc:
            if exc.code != -32601:
                raise
            entries = await ctx.registry.list_permission_profiles(cwd=cwd)
            state = "unsupported"
            hint = "installed Codex App Server does not support permissionProfile/list"
        else:
            entries = snapshot.profiles
            source = "app-server"
            state = "ready" if entries else "empty"
    else:
        entries = await ctx.registry.list_permission_profiles(cwd=cwd)
        state = "ready" if entries else "empty"
        if not entries:
            hint = "run dispatch permissions without --no-refresh to refresh the catalog"
    refreshed_at = max((entry.last_seen_at for entry in entries), default=None)
    if not inp.include_disallowed:
        entries = [entry for entry in entries if entry.allowed]
    now = datetime.fromisoformat(ctx.registry.now_iso())
    return PermissionProfilesOutput(
        cwd=cwd,
        refreshed_at=refreshed_at,
        source=source,
        catalog_state=state,
        hint=hint,
        profiles=[
            PermissionProfileItem(
                id=entry.id,
                description=entry.description,
                allowed=entry.allowed,
                source=entry.source,
                last_seen_at=entry.last_seen_at,
                freshness_seconds=_freshness_seconds(now, entry.last_seen_at),
            )
            for entry in entries
        ],
    )


def _freshness_seconds(now: datetime, observed_at: str | None) -> int | None:
    if observed_at is None:
        return None
    try:
        observed = datetime.fromisoformat(observed_at)
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return max(0, int((now - observed).total_seconds()))


async def usage(inp: UsageInput, ctx: Ctx) -> UsageOutput:
    refreshed: list[str] = []
    refresh_local_codex = (
        inp.refresh
        and inp.provider in {None, "codex"}
        and inp.host in {None, "local"}
        and inp.config_scope in {None, "default"}
    )
    if refresh_local_codex:
        await refresh_codex_capacity(ctx)
        refreshed.append("codex")
    observations = await ctx.registry.list_provider_capacity_observations(
        provider=inp.provider,
        host_scope=None if inp.all_hosts else inp.host,
    )
    if inp.config_scope is not None:
        observations = [
            observation
            for observation in observations
            if observation.config_scope == inp.config_scope
        ]
    now = datetime.fromisoformat(ctx.registry.now_iso())
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    views: list[UsageObservationView] = []
    for observation in observations:
        freshness = _freshness_seconds(now, observation.observed_at)
        account_freshness = _freshness_seconds(now, observation.account_observed_at)
        capacity_freshness = _freshness_seconds(now, observation.capacity_observed_at)
        usage_freshness = _freshness_seconds(now, observation.usage_observed_at)
        relevant_freshness = (
            capacity_freshness
            if observation.windows
            else account_freshness
            if observation.account_type is not None or observation.requires_auth is not None
            else freshness
        )
        windows: list[UsageWindowView] = []
        for window in observation.windows:
            window_freshness = _freshness_seconds(now, window.observed_at)
            windows.append(
                UsageWindowView(
                    **window.model_dump(exclude={"observed_at"}),
                    observed_at=window.observed_at,
                    freshness_seconds=window_freshness,
                    stale=window_freshness is None or window_freshness > inp.stale_after_seconds,
                )
            )
        views.append(
            UsageObservationView(
                provider=observation.provider,
                host=observation.host_scope,
                config_scope=observation.config_scope,
                state=observation.state,
                stale=relevant_freshness is None or relevant_freshness > inp.stale_after_seconds,
                freshness_seconds=freshness,
                account_freshness_seconds=account_freshness,
                capacity_freshness_seconds=capacity_freshness,
                usage_freshness_seconds=usage_freshness,
                account_type=observation.account_type,
                account_fingerprint=observation.account_fingerprint,
                account_label=observation.account_label,
                plan=observation.plan,
                requires_auth=observation.requires_auth,
                windows=windows,
                reset_credits_available=observation.reset_credits_available,
                reset_credits=observation.reset_credits,
                usage_summary=observation.usage_summary,
                daily_usage=observation.daily_usage if inp.include_daily else [],
                has_credits=observation.has_credits,
                unlimited_credits=observation.unlimited_credits,
                source=observation.source,
                observed_at=observation.observed_at,
                confidence=observation.confidence,
                error=observation.error,
            )
        )
    return UsageOutput(
        refreshed_providers=refreshed,
        observations=views,
        hint="run dispatch usage without --no-refresh to refresh local providers"
        if not views and not inp.refresh
        else None,
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
        input_modalities=entry.input_modalities,
        supports_personality=entry.supports_personality,
        upgrade=entry.upgrade,
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
    await ctx.registry.mark_provider_thread_state("codex", thread_id, "archived")
    await ctx.registry.log_action("archive", lane=thread_id)
    return _action_ref(thread_id=thread_id, lane=lane, status="archived")


def _is_no_rollout_archive_error(exc: ClientAppServerError) -> bool:
    return exc.code == -32600 and "no rollout found" in exc.message.lower()


async def restore(inp: ThreadTargetInput, ctx: Ctx) -> ThreadActionRef:
    thread_id, lane = await _resolve_thread_target(ctx, inp.target)
    thread = await ctx.client.thread_unarchive(thread_id)
    await observe_thread(
        ctx.registry, thread, lifecycle_state="active", relationship_source="thread/unarchive"
    )
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
    busy = sum(1 for lane in lanes if lane.status == "busy")
    waiting_approval = sum(1 for lane in lanes if lane.status == "waiting_approval")
    return StatusOutput(
        lanes=len(lanes),
        idle=sum(1 for lane in lanes if lane.status == "idle"),
        busy=busy,
        waiting_approval=waiting_approval,
        active=busy + waiting_approval,
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
