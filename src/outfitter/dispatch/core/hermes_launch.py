"""Durable Hermes creation coordination over the shared registry primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from outfitter.dispatch.client.hermes import HermesSessionCreated
from outfitter.dispatch.config import DEFAULT_HERMES_BINDING_ID
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    DeliveryConflictError,
    ValidationError,
)
from outfitter.dispatch.registry.delivery import DeliveryReceipt
from outfitter.dispatch.registry.launch import LaneLaunch
from outfitter.dispatch.registry.models import Lane

from .delivery import encode_prepared_request, submit_reserved
from .launch import ResolvedLaunch
from .models import NewInput
from .providers import PreparedProviderRequest, ProviderAction, ProviderTarget, router_for


@dataclass(frozen=True)
class HermesLaunchOutcome:
    lane: Lane
    launch: LaneLaunch
    delivery: DeliveryReceipt | None
    created: bool


def submitted_launch_payload(inp: NewInput) -> str:
    """Canonical raw caller intent, excluding the key and trusted caller identity."""

    value = inp.model_dump(
        mode="json",
        exclude={"idempotency_key", "caller_thread_id"},
    )
    return _canonical_json({"version": 1, "input": value})


async def find_launch_replay(inp: NewInput, ctx: Ctx) -> HermesLaunchOutcome | None:
    """Resolve an exact keyed launch before mutable configuration or provider reads."""

    if inp.idempotency_key is None:
        return None
    binding = await ctx.registry.get_caller_key_binding(inp.idempotency_key)
    if binding is None:
        return None
    kind, record = binding
    if kind == "delivery":
        raise DeliveryConflictError(
            f"lane launch key {inp.idempotency_key!r} is already bound to a delivery"
        )
    assert isinstance(record, LaneLaunch)
    launch = record
    if launch.submitted_payload != submitted_launch_payload(inp):
        raise DeliveryConflictError(
            f"lane launch key {inp.idempotency_key!r} is already bound to different input"
        )
    lane = await ctx.registry.get_lane(launch.lane)
    delivery = (
        await ctx.registry.get_delivery(launch.first_delivery_id)
        if launch.first_delivery_id is not None
        else None
    )
    return HermesLaunchOutcome(lane=lane, launch=launch, delivery=delivery, created=False)


def validate_hermes_launch(inp: NewInput, launch: ResolvedLaunch) -> None:
    """Keep the first adapter slice to owned plain-text sessions at one cwd."""

    unsupported: list[str] = []
    if inp.cwd is None:
        unsupported.append("cwd (must be explicit)")
    elif not launch.resolved.cwd.is_dir():
        raise ValidationError("Hermes launch cwd must be an existing directory")
    if launch.goal is not None:
        unsupported.append("goal")
    if launch.content:
        unsupported.append("content")
    if launch.output_schema is not None:
        unsupported.append("output_schema")
    if launch.resolved.base_instructions is not None:
        unsupported.append("base_instructions")
    if launch.resolved.developer_instructions is not None:
        unsupported.append("developer_instructions")
    if launch.stage_plan.parts:
        unsupported.append("stage")
    if inp.workspace is not None or inp.workspace_setup != "auto":
        unsupported.append("workspace")
    if any(
        value is not None
        for value in (inp.worktree, inp.worktree_path, inp.worktree_branch, inp.worktree_base)
    ):
        unsupported.append("worktree")
    if inp.subscribe is not None:
        unsupported.append("subscribe")
    if unsupported:
        raise ValidationError(
            "Hermes launch currently supports name, cwd, presets, prefix, and plain text; "
            "unsupported input(s): " + ", ".join(sorted(set(unsupported)))
        )


async def create_hermes_lane(
    inp: NewInput,
    launch: ResolvedLaunch,
    ctx: Ctx,
) -> HermesLaunchOutcome:
    route = router_for(ctx).route_hermes_launch()
    route.recheck()
    generation = route.availability.generation
    if generation is None:
        raise AppServerError("ready Hermes binding omitted its connection generation")

    submitted = submitted_launch_payload(inp)
    request_payload = _canonical_json(
        {
            "version": 1,
            "provider": "hermes",
            "binding_id": DEFAULT_HERMES_BINDING_ID,
            "generation": generation,
            "name": launch.resolved.display_name,
            "handle": launch.resolved.handle,
            "cwd": str(launch.resolved.cwd),
            "text": launch.text,
            "send": launch.would_send,
        }
    )
    launch_record, lane, created = await ctx.registry.reserve_lane_launch(
        key=inp.idempotency_key,
        submitted_payload=submitted,
        request_payload=request_payload,
        handle=launch.resolved.handle,
        cwd=str(launch.resolved.cwd),
        provider="hermes",
        binding_id=DEFAULT_HERMES_BINDING_ID,
        generation=generation,
    )
    if not created:
        existing_delivery = (
            await ctx.registry.get_delivery(launch_record.first_delivery_id)
            if launch_record.first_delivery_id is not None
            else None
        )
        return HermesLaunchOutcome(lane, launch_record, existing_delivery, False)
    if not await ctx.registry.claim_lane_launch(lane.id, generation=generation):
        raise AppServerError(f"Hermes creation reservation for {lane.ref} is held")

    route.recheck()
    result = await route.adapter.create_session(
        lane_id=lane.id,
        cwd=str(launch.resolved.cwd),
        title=launch.resolved.display_name,
    )
    if not isinstance(result, HermesSessionCreated):
        launch_record = await ctx.registry.mark_lane_launch_ambiguous(
            lane.id,
            generation=generation,
            error=result.error[:2000],
        )
        raise AppServerError(
            f"Hermes session creation outcome is unknown for {lane.ref}: {launch_record.error}"
        )

    route.recheck()
    launch_record = await ctx.registry.record_lane_launch_mapping(
        lane.id,
        generation=generation,
        runtime_session_id=result.runtime_session_id,
        stored_session_id=result.stored_session_id,
        effective_cwd=result.effective_cwd,
        first_delivery_required=launch.would_send,
    )
    delivery: DeliveryReceipt | None = None
    if launch.would_send:
        assert launch.text is not None
        delivery_id = str(uuid4())
        prepared = PreparedProviderRequest(
            target=ProviderTarget(
                lane_id=lane.id,
                provider="hermes",
                binding_id=DEFAULT_HERMES_BINDING_ID,
                native_session_id=result.stored_session_id,
                runtime_session_id=result.runtime_session_id,
                generation=generation,
            ),
            action=ProviderAction.SEND,
            transport="turn",
            correlation_id=delivery_id,
            text=launch.text,
            cwd=result.effective_cwd,
        )
        delivery = await ctx.registry.reserve_lane_launch_first_delivery(
            lane.id,
            submitted_payload=_canonical_json(
                {"version": 1, "lane": lane.id, "text": launch.text, "mode": "send"}
            ),
            payload=encode_prepared_request(prepared),
            text=launch.text,
            delivery_id=delivery_id,
        )
        await submit_reserved(delivery.id, ctx)
        delivery = await ctx.registry.get_delivery(delivery.id)
    lane = await ctx.registry.get_lane(lane.id)
    launch_record = await ctx.registry.get_lane_launch(lane.id)
    return HermesLaunchOutcome(lane, launch_record, delivery, True)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
