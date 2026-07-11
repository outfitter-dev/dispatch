"""Codex permission-profile discovery and launch validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from outfitter.dispatch.client.errors import AppServerError
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import ValidationError
from outfitter.dispatch.registry.models import PermissionProfileEntry


@dataclass(frozen=True)
class PermissionProfileSnapshot:
    profiles: list[PermissionProfileEntry]
    refreshed_at: str


async def refresh_permission_profiles(ctx: Ctx, *, cwd: str) -> PermissionProfileSnapshot:
    cwd = str(Path(cwd).expanduser().resolve())
    profiles = await ctx.client.permission_profile_list(cwd=cwd)
    now = ctx.registry.now_iso()
    entries = [
        PermissionProfileEntry(
            id=profile.id,
            cwd=cwd,
            description=profile.description,
            allowed=profile.allowed,
            first_seen_at=now,
            last_seen_at=now,
        )
        for profile in profiles
    ]
    await ctx.registry.replace_permission_profiles(cwd, entries)
    return PermissionProfileSnapshot(profiles=entries, refreshed_at=now)


async def resolve_permission_profile(ctx: Ctx, profile_id: str | None, *, cwd: str) -> str | None:
    if profile_id is None:
        return None
    try:
        snapshot = await refresh_permission_profiles(ctx, cwd=cwd)
    except AppServerError as exc:
        if exc.code == -32601:
            raise ValidationError(
                "permission profiles are not supported by the installed Codex App Server"
            ) from exc
        raise
    profile = next((item for item in snapshot.profiles if item.id == profile_id), None)
    allowed = [item.id for item in snapshot.profiles if item.allowed]
    choices = ", ".join(allowed) or "none"
    if profile is None:
        raise ValidationError(
            f"unknown permission profile {profile_id!r}; available profiles: {choices}"
        )
    if not profile.allowed:
        raise ValidationError(
            f"permission profile {profile_id!r} is not allowed; available profiles: {choices}"
        )
    return profile.id
