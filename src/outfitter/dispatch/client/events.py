"""Normalized internal LaneEvent vocabulary + projection (ADR-0007).

The client layer is the single point that turns raw App Server notifications and
server-requests into typed ``LaneEvent``s. The reactor, triggers, and the
conditional-guard seam operate ONLY on these — never on raw protocol dicts — so
they stay stable across binary/protocol drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ApprovalKind = Literal["command", "file_change"]


@dataclass(frozen=True)
class LaneEvent:
    """Base for all normalized lane events. ``lane_id`` is the App Server threadId."""

    lane_id: str


@dataclass(frozen=True)
class TurnStarted(LaneEvent):
    turn_id: str | None = None


@dataclass(frozen=True)
class TurnCompleted(LaneEvent):
    turn_id: str | None = None


@dataclass(frozen=True)
class TurnFailed(LaneEvent):
    turn_id: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class LaneIdle(LaneEvent):
    """Derived: the lane has no active flags (computed here, not per-consumer)."""


@dataclass(frozen=True)
class ApprovalRequested(LaneEvent):
    """A server->client approval request. ``request_id`` is the JSON-RPC id to
    reply on; file-change requests carry no diff (correlate by ``item_id``)."""

    request_id: int
    kind: ApprovalKind
    item_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class ItemCompleted(LaneEvent):
    item_id: str | None = None


@dataclass(frozen=True)
class DiffUpdated(LaneEvent):
    turn_id: str | None = None


@dataclass(frozen=True)
class StatusChanged(LaneEvent):
    active_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenUsageUpdated(LaneEvent):
    pass


@dataclass(frozen=True)
class GoalUpdated(LaneEvent):
    pass


@dataclass(frozen=True)
class GoalCleared(LaneEvent):
    pass


@dataclass(frozen=True)
class ThreadCompacted(LaneEvent):
    pass


def _thread_id(params: dict[str, object]) -> str | None:
    tid = params.get("threadId")
    return tid if isinstance(tid, str) else None


def _str(params: dict[str, object], key: str) -> str | None:
    val = params.get(key)
    return val if isinstance(val, str) else None


def project_notification(method: str, params: dict[str, object]) -> list[LaneEvent]:
    """Project a server->client notification into zero or more LaneEvents.

    Unknown methods project to ``[]`` (ignored), keeping triggers decoupled from
    the long tail of protocol notifications.
    """
    lane = _thread_id(params)
    if lane is None:
        return []
    turn = _str(params, "turnId")
    match method:
        case "turn/started":
            return [TurnStarted(lane, turn)]
        case "turn/completed":
            return [TurnCompleted(lane, turn)]
        case "turn/failed":
            return [TurnFailed(lane, turn, _str(params, "message"))]
        case "turn/diff/updated":
            return [DiffUpdated(lane, turn)]
        case "item/completed":
            return [ItemCompleted(lane, _str(params, "itemId"))]
        case "thread/tokenUsage/updated":
            return [TokenUsageUpdated(lane)]
        case "thread/goal/updated":
            return [GoalUpdated(lane)]
        case "thread/goal/cleared":
            return [GoalCleared(lane)]
        case "thread/compacted":
            return [ThreadCompacted(lane)]
        case "thread/status/changed":
            flags = _active_flags(params)
            events: list[LaneEvent] = [StatusChanged(lane, flags)]
            if not flags:
                events.append(LaneIdle(lane))
            return events
        case _:
            return []


def project_server_request(
    request_id: int, method: str, params: dict[str, object]
) -> LaneEvent | None:
    """Project a server->client request (approvals) into a LaneEvent, or None."""
    lane = _thread_id(params)
    if lane is None:
        return None
    item = _str(params, "itemId")
    turn = _str(params, "turnId")
    match method:
        case "item/commandExecution/requestApproval":
            return ApprovalRequested(lane, request_id, "command", item, turn)
        case "item/fileChange/requestApproval":
            return ApprovalRequested(lane, request_id, "file_change", item, turn)
        case _:
            return None


def _active_flags(params: dict[str, object]) -> tuple[str, ...]:
    status = params.get("status")
    flags = status.get("activeFlags") if isinstance(status, dict) else params.get("activeFlags")
    if isinstance(flags, list):
        return tuple(f for f in flags if isinstance(f, str))
    return ()
