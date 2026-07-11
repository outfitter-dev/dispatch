"""Typed async App Server client (importable standalone, no daemon dependency).

The only layer that spawns or speaks to ``codex app-server``. Exposes the typed
primitives, the normalized ``LaneEvent`` vocabulary, and the client exceptions.
"""

from __future__ import annotations

from .client import AppServerClient
from .errors import AppServerError, ClientError, ProtocolError, TransportError
from .events import (
    ApprovalRequested,
    DiffUpdated,
    ItemCompleted,
    ItemStarted,
    LaneEvent,
    LaneIdle,
    ServerRequestReceived,
    StatusChanged,
    ThreadDeleted,
    ThreadStarted,
    TokenUsageUpdated,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from .models import InitializeResult, JsonRpcError, SandboxPolicy, ThreadInfo
from .transport import StdioTransport, Transport

__all__ = [
    "AppServerClient",
    "AppServerError",
    "ApprovalRequested",
    "ClientError",
    "DiffUpdated",
    "InitializeResult",
    "ItemCompleted",
    "ItemStarted",
    "JsonRpcError",
    "LaneEvent",
    "LaneIdle",
    "ProtocolError",
    "SandboxPolicy",
    "ServerRequestReceived",
    "StatusChanged",
    "StdioTransport",
    "ThreadDeleted",
    "ThreadInfo",
    "ThreadStarted",
    "TokenUsageUpdated",
    "Transport",
    "TransportError",
    "TurnCompleted",
    "TurnFailed",
    "TurnStarted",
]
