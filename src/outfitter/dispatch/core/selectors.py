"""Shared selector resolution for managed and unmanaged Codex threads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import NotFoundError, ValidationError
from outfitter.dispatch.registry.models import Lane

SelectorKind = Literal["ref", "thread_id", "lane_id", "handle", "title", "fuzzy_title", "raw"]


@dataclass(frozen=True)
class ResolvedTarget:
    selector: str
    kind: SelectorKind
    thread_id: str
    lane: Lane | None = None

    @property
    def ref(self) -> str | None:
        return self.lane.ref if self.lane is not None else None

    @property
    def title(self) -> str | None:
        return self.lane.handle if self.lane is not None else None

    @property
    def managed(self) -> bool:
        return self.lane is not None


async def resolve_managed_selector(
    ctx: Ctx, selector: str, *, allow_fuzzy: bool = False
) -> ResolvedTarget:
    resolved = await resolve_thread_selector(
        ctx,
        selector,
        allow_unmanaged_raw=False,
        allow_fuzzy=allow_fuzzy,
    )
    if resolved.lane is None:
        raise NotFoundError(f"no managed thread {selector!r}")
    return resolved


async def resolve_thread_selector(
    ctx: Ctx,
    selector: str,
    *,
    allow_unmanaged_raw: bool,
    allow_fuzzy: bool = False,
) -> ResolvedTarget:
    lane = await ctx.registry.find_lane_by_ref(selector)
    if lane is not None:
        return ResolvedTarget(selector=selector, kind="ref", thread_id=lane.id, lane=lane)

    lane = await ctx.registry.find_lane(selector)
    if lane is not None:
        return ResolvedTarget(selector=selector, kind="thread_id", thread_id=lane.id, lane=lane)

    handle_matches = await ctx.registry.find_lanes_by_handle(selector)
    if len(handle_matches) == 1:
        lane = handle_matches[0]
        return ResolvedTarget(selector=selector, kind="handle", thread_id=lane.id, lane=lane)
    if len(handle_matches) > 1:
        _raise_ambiguous(selector, handle_matches)

    title_matches = _unique_lanes(await ctx.registry.find_lanes_by_title(selector))
    if len(title_matches) == 1:
        lane = title_matches[0]
        return ResolvedTarget(selector=selector, kind="title", thread_id=lane.id, lane=lane)
    if len(title_matches) > 1:
        _raise_ambiguous(selector, title_matches)

    if allow_fuzzy:
        fuzzy_matches = _unique_lanes(await ctx.registry.fuzzy_find_lanes_by_title(selector))
        if len(fuzzy_matches) == 1:
            lane = fuzzy_matches[0]
            return ResolvedTarget(
                selector=selector, kind="fuzzy_title", thread_id=lane.id, lane=lane
            )
        if len(fuzzy_matches) > 1:
            _raise_ambiguous(selector, fuzzy_matches)

    if selector.startswith("@"):
        raise NotFoundError(f"no managed thread {selector!r}")
    if allow_unmanaged_raw:
        return ResolvedTarget(selector=selector, kind="raw", thread_id=selector)
    raise NotFoundError(f"no managed thread {selector!r}")


def _unique_lanes(lanes: list[Lane]) -> list[Lane]:
    by_id: dict[str, Lane] = {}
    for lane in lanes:
        by_id.setdefault(lane.id, lane)
    return list(by_id.values())


def _raise_ambiguous(selector: str, lanes: list[Lane]) -> None:
    candidates = [
        {
            "ref": lane.ref,
            "id_prefix": lane.id[:8],
            "title": lane.handle,
            "managed": True,
            "cwd": lane.cwd,
        }
        for lane in lanes
    ]
    raise ValidationError(f"ambiguous selector {selector!r}; candidates={candidates!r}")
