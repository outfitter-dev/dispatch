"""Normalized internal LaneEvent vocabulary + projection (ADR-0007).

The client layer is the single point that turns raw App Server notifications and
server-requests into typed ``LaneEvent``s. The reactor, triggers, and the
conditional-guard seam operate ONLY on these — never on raw protocol dicts — so
they stay stable across binary/protocol drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ApprovalKind = Literal["command", "file_change"]


@dataclass(frozen=True)
class LaneEvent:
    """Base for all normalized lane events. ``lane_id`` is the App Server threadId."""

    lane_id: str
    raw_payload: dict[str, object] | None = field(
        default=None,
        compare=False,
        kw_only=True,
    )


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


@dataclass(frozen=True)
class ThreadArchived(LaneEvent):
    pass


@dataclass(frozen=True)
class ThreadUnarchived(LaneEvent):
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
    raw: dict[str, object] = {"method": method, "params": params}
    match method:
        case "turn/started":
            return [TurnStarted(lane, turn, raw_payload=raw)]
        case "turn/completed":
            return [TurnCompleted(lane, turn, raw_payload=raw)]
        case "turn/failed":
            return [TurnFailed(lane, turn, _str(params, "message"), raw_payload=raw)]
        case "turn/diff/updated":
            return [DiffUpdated(lane, turn, raw_payload=raw)]
        case "item/completed":
            return [ItemCompleted(lane, _str(params, "itemId"), raw_payload=raw)]
        case "thread/tokenUsage/updated":
            return [TokenUsageUpdated(lane, raw_payload=raw)]
        case "thread/goal/updated":
            return [GoalUpdated(lane, raw_payload=raw)]
        case "thread/goal/cleared":
            return [GoalCleared(lane, raw_payload=raw)]
        case "thread/compacted":
            return [ThreadCompacted(lane, raw_payload=raw)]
        case "thread/archived":
            return [ThreadArchived(lane, raw_payload=raw)]
        case "thread/unarchived":
            return [ThreadUnarchived(lane, raw_payload=raw)]
        case "thread/status/changed":
            flags = _active_flags(params)
            events: list[LaneEvent] = [StatusChanged(lane, flags, raw_payload=raw)]
            if not flags:
                events.append(LaneIdle(lane, raw_payload=raw))
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
    raw: dict[str, object] = {"id": request_id, "method": method, "params": params}
    match method:
        case "item/commandExecution/requestApproval":
            return ApprovalRequested(lane, request_id, "command", item, turn, raw_payload=raw)
        case "item/fileChange/requestApproval":
            return ApprovalRequested(lane, request_id, "file_change", item, turn, raw_payload=raw)
        case _:
            return None


def _active_flags(params: dict[str, object]) -> tuple[str, ...]:
    status = params.get("status")
    flags = status.get("activeFlags") if isinstance(status, dict) else params.get("activeFlags")
    if isinstance(flags, list):
        return tuple(f for f in flags if isinstance(f, str))
    return ()
