"""Typed Pydantic wire models for the App Server JSON-RPC-lite protocol.

Validation at the boundary (``.claude/rules/python-conventions.md``): every
message dispatch sends or parses goes through a model here. Python fields are
snake_case; the wire is camelCase (alias generator). Serialize outbound params
with ``model_dump(by_alias=True, exclude_none=True)``.

Encodes the verified gotchas (``docs/research/app-server-verification.md``):
- ``thread/start.sandbox`` is a STRING enum; ``turn/start.sandboxPolicy`` is an
  OBJECT — two different encodings, modelled separately.
- ``turn/steer`` requires ``expectedTurnId``.
- ``thread/list`` results live under ``result.data`` (not ``result.threads``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

ThreadSandbox = Literal["read-only", "workspace-write", "danger-full-access"]
ApprovalPolicy = Literal["never", "untrusted", "on-request", "on-failure"]
ApprovalsReviewer = Literal["user", "auto_review", "guardian_subagent"]
SandboxPolicyType = Literal["readOnly", "workspaceWrite", "externalSandbox", "dangerFullAccess"]
Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
ReasoningSummary = Literal["auto", "concise", "detailed", "none"]
Personality = Literal["none", "friendly", "pragmatic"]
Decision = Literal["accept", "acceptForSession", "decline", "cancel"]


class WireModel(BaseModel):
    """Base for wire models: camelCase aliases, accept either casing, ignore
    unknown fields (the protocol carries far more than dispatch consumes)."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


# --- initialize ---------------------------------------------------------------


class ClientInfo(WireModel):
    name: str
    version: str


class InitializeParams(WireModel):
    client_info: ClientInfo
    capabilities: dict[str, bool] = {}


class InitializeResult(WireModel):
    user_agent: str | None = None
    codex_home: str | None = None
    platform_family: str | None = None
    platform_os: str | None = None


# --- shared shapes ------------------------------------------------------------


class TextInput(WireModel):
    type: Literal["text"] = "text"
    text: str


class SandboxPolicy(WireModel):
    """Object form used by ``turn/start.sandboxPolicy`` (NOT the string enum)."""

    type: SandboxPolicyType = "readOnly"


class ThreadStatus(WireModel):
    """Thread status is an OBJECT (e.g. ``{"type": "idle"}``), not a string —
    verified against the live binary."""

    type: str | None = None
    active_flags: list[str] = []


class ThreadInfo(WireModel):
    """Subset of the rich thread object the server returns (extra fields ignored)."""

    id: str
    ephemeral: bool | None = None
    status: ThreadStatus | None = None
    cwd: str | None = None
    name: str | None = None
    preview: str | None = None
    source: str | None = None


# --- thread/* params + results ------------------------------------------------


class ThreadStartParams(WireModel):
    cwd: str | None = None
    sandbox: ThreadSandbox = "read-only"
    approval_policy: ApprovalPolicy = "never"
    approvals_reviewer: ApprovalsReviewer | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    personality: Personality | None = None
    service_tier: str | None = None
    model: str | None = None
    model_provider: str | None = None
    ephemeral: bool = False


class ThreadResumeParams(WireModel):
    thread_id: str


class ThreadSetNameParams(WireModel):
    thread_id: str
    name: str


class ThreadListParams(WireModel):
    limit: int = 50
    cursor: str | None = None
    use_state_db_only: bool | None = None


class ThreadReadParams(WireModel):
    thread_id: str


class ThreadArchiveParams(WireModel):
    thread_id: str


class ThreadResult(WireModel):
    """Result envelope for ``thread/start`` and ``thread/resume``."""

    thread: ThreadInfo


class ThreadListResult(WireModel):
    """``thread/list`` returns rows under ``result.data`` (verified gotcha)."""

    data: list[ThreadInfo] = []
    next_cursor: str | None = None


# --- turn/* + inject_items params ---------------------------------------------


class TurnStartParams(WireModel):
    thread_id: str
    input: list[TextInput]
    cwd: str
    approval_policy: ApprovalPolicy = "never"
    approvals_reviewer: ApprovalsReviewer | None = None
    sandbox_policy: SandboxPolicy = SandboxPolicy()
    effort: Effort | None = None
    summary: ReasoningSummary | None = None
    model: str | None = None
    output_schema: dict[str, object] | None = None
    personality: Personality | None = None


class TurnSteerParams(WireModel):
    thread_id: str
    expected_turn_id: str
    input: list[TextInput]


class TurnInterruptParams(WireModel):
    thread_id: str
    turn_id: str | None = None


class InjectItemsParams(WireModel):
    thread_id: str
    items: list[dict[str, object]]
