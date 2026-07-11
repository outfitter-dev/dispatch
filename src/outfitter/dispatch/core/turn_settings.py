"""Helpers for reusing a lane's saved turn-start runtime settings."""

from __future__ import annotations

from dataclasses import dataclass

from outfitter.dispatch.client.models import (
    ApprovalPolicy,
    ApprovalsReviewer,
    Effort,
    Personality,
    ReasoningSummary,
    SandboxPolicy,
    ThreadSandbox,
)
from outfitter.dispatch.registry.models import LaneRuntimeSettings
from outfitter.dispatch.registry.store import Registry


@dataclass(frozen=True)
class TurnStartSettings:
    permission_profile: str | None = None
    sandbox_policy: SandboxPolicy | None = None
    approval_policy: ApprovalPolicy | None = None
    approvals_reviewer: ApprovalsReviewer | None = None
    effort: Effort | None = None
    summary: ReasoningSummary | None = None
    model: str | None = None
    service_tier: str | None = None
    output_schema: dict[str, object] | None = None
    personality: Personality | None = None


def thread_sandbox_to_turn_policy(sandbox: ThreadSandbox) -> SandboxPolicy:
    match sandbox:
        case "read-only":
            return SandboxPolicy(type="readOnly")
        case "workspace-write":
            return SandboxPolicy(type="workspaceWrite")
        case "danger-full-access":
            return SandboxPolicy(type="dangerFullAccess")


def runtime_settings_for_lane(
    *,
    lane: str,
    updated_at: str,
    permission_profile: str | None = None,
    sandbox: ThreadSandbox | None = None,
    approval_policy: ApprovalPolicy | None = None,
    approvals_reviewer: ApprovalsReviewer | None = None,
    effort: Effort | None = None,
    summary: ReasoningSummary | None = None,
    model: str | None = None,
    service_tier: str | None = None,
    output_schema: dict[str, object] | None = None,
    personality: Personality | None = None,
) -> LaneRuntimeSettings:
    return LaneRuntimeSettings(
        lane=lane,
        permission_profile=permission_profile,
        sandbox=sandbox,
        approval_policy=approval_policy,
        approvals_reviewer=approvals_reviewer,
        effort=effort,
        summary=summary,
        model=model,
        service_tier=service_tier,
        output_schema=output_schema,
        personality=personality,
        updated_at=updated_at,
    )


async def load_turn_start_settings(registry: Registry, lane_id: str) -> TurnStartSettings:
    stored = await registry.get_lane_runtime_settings(lane_id)
    if stored is None:
        return TurnStartSettings()
    return TurnStartSettings(
        permission_profile=stored.permission_profile,
        sandbox_policy=(
            thread_sandbox_to_turn_policy(stored.sandbox) if stored.sandbox is not None else None
        ),
        approval_policy=stored.approval_policy,
        approvals_reviewer=stored.approvals_reviewer,
        effort=stored.effort,
        summary=stored.summary,
        model=stored.model,
        service_tier=stored.service_tier,
        output_schema=stored.output_schema,
        personality=stored.personality,
    )
