"""Normalized internal LaneEvent vocabulary + projection (ADR-0007).

The client layer is the single point that turns raw App Server notifications and
server-requests into typed ``LaneEvent``s. The reactor, triggers, and the
conditional-guard seam operate ONLY on these — never on raw protocol dicts — so
they stay stable across binary/protocol drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .models import JsonRpcId, ServerRequestCategory

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

    request_id: JsonRpcId
    kind: ApprovalKind
    item_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class ItemStarted(LaneEvent):
    item_id: str | None = None
    turn_id: str | None = None
    item: dict[str, object] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ItemCompleted(LaneEvent):
    item_id: str | None = None
    turn_id: str | None = None
    item: dict[str, object] | None = field(default=None, compare=False)


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


@dataclass(frozen=True)
class ServerRequestReceived:
    """A server->client JSON-RPC request, independent of a lane event.

    Auth and attestation requests can be threadless. Raw params stay in memory
    only and are omitted from the repr to reduce accidental credential exposure.
    """

    method: str
    request_id: JsonRpcId
    category: ServerRequestCategory
    thread_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    raw_params: dict[str, object] = field(compare=False, repr=False, default_factory=dict)

    @property
    def lane_id(self) -> str | None:
        """Return the thread key, including the legacy ``conversationId`` form."""

        return self.thread_id or self.conversation_id


def _thread_id(params: dict[str, object]) -> str | None:
    tid = params.get("threadId")
    return tid if isinstance(tid, str) else None


def _conversation_id(params: dict[str, object]) -> str | None:
    cid = params.get("conversationId")
    return cid if isinstance(cid, str) else None


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
        case "item/started" | "item/completed":
            item = params.get("item")
            canonical_item = item if isinstance(item, dict) else None
            item_id = (
                _str(canonical_item, "id") if canonical_item is not None else _str(params, "itemId")
            )
            event_type = ItemStarted if method == "item/started" else ItemCompleted
            return [
                event_type(
                    lane,
                    item_id,
                    turn,
                    canonical_item,
                    raw_payload=raw,
                )
            ]
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


_SERVER_REQUEST_CATEGORIES: dict[str, ServerRequestCategory] = {
    "account/chatgptAuthTokens/refresh": "auth",
    "applyPatchApproval": "approval",
    "attestation/generate": "attestation",
    "execCommandApproval": "approval",
    "item/commandExecution/requestApproval": "approval",
    "item/fileChange/requestApproval": "approval",
    "item/permissions/requestApproval": "approval",
    "item/tool/call": "tool_call",
    "item/tool/requestUserInput": "user_input",
    "mcpServer/elicitation/request": "elicitation",
}


def classify_server_request(method: str) -> ServerRequestCategory:
    """Classify known App Server server-request methods, preserving unknowns."""

    return _SERVER_REQUEST_CATEGORIES.get(method, "unknown")


def project_server_request_received(
    request_id: JsonRpcId, method: str, params: dict[str, object]
) -> ServerRequestReceived:
    """Normalize every server->client request, including threadless requests."""

    return ServerRequestReceived(
        method=method,
        request_id=request_id,
        category=classify_server_request(method),
        thread_id=_thread_id(params),
        conversation_id=_conversation_id(params),
        turn_id=_str(params, "turnId"),
        item_id=_str(params, "itemId"),
        raw_params=params,
    )


def project_approval_request(request: ServerRequestReceived) -> ApprovalRequested | None:
    """Retain the normalized lane-event projection for supported approvals."""

    lane = request.lane_id
    if lane is None:
        return None
    raw: dict[str, object] = {
        "id": request.request_id,
        "method": request.method,
        "params": request.raw_params,
    }
    match request.method:
        case "item/commandExecution/requestApproval":
            return ApprovalRequested(
                lane,
                request.request_id,
                "command",
                request.item_id,
                request.turn_id,
                raw_payload=raw,
            )
        case "item/fileChange/requestApproval":
            return ApprovalRequested(
                lane,
                request.request_id,
                "file_change",
                request.item_id,
                request.turn_id,
                raw_payload=raw,
            )
        case _:
            return None


def project_server_request(
    request_id: JsonRpcId, method: str, params: dict[str, object]
) -> ApprovalRequested | None:
    """Compatibility wrapper for the legacy approval LaneEvent projection."""

    return project_approval_request(project_server_request_received(request_id, method, params))


def _active_flags(params: dict[str, object]) -> tuple[str, ...]:
    status = params.get("status")
    flags = status.get("activeFlags") if isinstance(status, dict) else params.get("activeFlags")
    if isinstance(flags, list):
        return tuple(f for f in flags if isinstance(f, str))
    return ()
