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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

ThreadSandbox = Literal["read-only", "workspace-write", "danger-full-access"]
ApprovalPolicy = Literal["never", "untrusted", "on-request", "on-failure"]
ApprovalsReviewer = Literal["user", "auto_review", "guardian_subagent"]
SandboxPolicyType = Literal["readOnly", "workspaceWrite", "externalSandbox", "dangerFullAccess"]
Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
ReasoningSummary = Literal["auto", "concise", "detailed", "none"]
Personality = Literal["none", "friendly", "pragmatic"]
Decision = Literal["accept", "acceptForSession", "decline", "cancel"]
SortDirection = Literal["asc", "desc"]
ThreadSortKey = Literal["created_at", "updated_at"]
ThreadListCwdFilter = str | list[str]
ThreadSourceKind = Literal[
    "cli",
    "vscode",
    "exec",
    "appServer",
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
    "unknown",
]
ThreadGoalStatus = Literal[
    "active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"
]


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


# --- config/model catalog ------------------------------------------------------


class ConfigInfo(WireModel):
    """Subset of ``config/read`` used for model/service-tier defaults."""

    model: str | None = None
    model_provider: str | None = None
    service_tier: str | None = None
    model_reasoning_effort: str | None = None


class ModelServiceTier(WireModel):
    id: str
    name: str
    description: str


class AppModel(WireModel):
    """Subset of one ``model/list`` row.

    ``additionalSpeedTiers`` is deprecated by the app-server schema, but kept as
    a fallback for older binaries. ``serviceTiers`` is the canonical source.
    """

    id: str
    model: str | None = None
    display_name: str | None = None
    name: str | None = None
    description: str | None = None
    is_default: bool | None = None
    hidden: bool | None = None
    default_reasoning_effort: str | None = None
    supported_reasoning_efforts: list[str] = Field(default_factory=list)
    default_service_tier: str | None = None
    service_tiers: list[ModelServiceTier] = Field(default_factory=list)
    additional_speed_tiers: list[str] = Field(default_factory=list)

    @field_validator("supported_reasoning_efforts", mode="before")
    @classmethod
    def _normalize_supported_reasoning_efforts(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        efforts: list[str] = []
        for item in value:
            if isinstance(item, str):
                efforts.append(item)
            elif isinstance(item, dict):
                effort = item.get("reasoningEffort") or item.get("effort") or item.get("id")
                if isinstance(effort, str):
                    efforts.append(effort)
        return efforts


class ModelListResult(WireModel):
    data: list[AppModel] = Field(default_factory=list)
    next_cursor: str | None = None


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
    session_id: str | None = None
    forked_from_id: str | None = None
    parent_thread_id: str | None = None
    ephemeral: bool | None = None
    status: ThreadStatus | None = None
    cwd: str | None = None
    name: str | None = None
    path: str | None = None
    preview: str | None = None
    source: str | None = None
    thread_source: str | None = None
    model_provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    turns: list[dict[str, object]] = Field(default_factory=list)


# --- thread/* params + results ------------------------------------------------


class ThreadStartParams(WireModel):
    cwd: str | None = None
    sandbox: ThreadSandbox | None = None
    approval_policy: ApprovalPolicy | None = None
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
    exclude_turns: bool | None = None


class ThreadForkParams(WireModel):
    thread_id: str
    cwd: str | None = None
    sandbox: ThreadSandbox | None = None
    approval_policy: ApprovalPolicy | None = None
    approvals_reviewer: ApprovalsReviewer | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    service_tier: str | None = None
    model: str | None = None
    model_provider: str | None = None
    ephemeral: bool = False


class ThreadSetNameParams(WireModel):
    thread_id: str
    name: str


class ThreadListParams(WireModel):
    limit: int = 50
    cursor: str | None = None
    archived: bool | None = None
    cwd: ThreadListCwdFilter | None = None
    model_providers: list[str] | None = None
    search_term: str | None = None
    sort_direction: SortDirection | None = None
    sort_key: ThreadSortKey | None = None
    source_kinds: list[ThreadSourceKind] | None = None
    use_state_db_only: bool | None = None


class ThreadReadParams(WireModel):
    thread_id: str
    include_turns: bool = False


class ThreadArchiveParams(WireModel):
    thread_id: str


class ThreadUnarchiveParams(WireModel):
    thread_id: str


class ThreadSearchParams(WireModel):
    search_term: str
    archived: bool | None = None
    cursor: str | None = None
    limit: int | None = None
    sort_direction: SortDirection | None = None
    sort_key: ThreadSortKey | None = None
    source_kinds: list[ThreadSourceKind] | None = None


class ThreadRollbackParams(WireModel):
    thread_id: str
    num_turns: int


class ThreadCompactStartParams(WireModel):
    thread_id: str


class ThreadGoalSetParams(WireModel):
    thread_id: str
    objective: str | None = None
    status: ThreadGoalStatus | None = None
    token_budget: int | None = None


class ThreadGoalGetParams(WireModel):
    thread_id: str


class ThreadGoalClearParams(WireModel):
    thread_id: str


class ThreadGoal(WireModel):
    thread_id: str
    objective: str
    status: ThreadGoalStatus
    tokens_used: int
    time_used_seconds: int
    created_at: int
    updated_at: int
    token_budget: int | None = None


class ThreadResult(WireModel):
    """Result envelope for thread methods that return one thread."""

    thread: ThreadInfo


class ThreadListResult(WireModel):
    """``thread/list`` returns rows under ``result.data`` (verified gotcha)."""

    data: list[ThreadInfo] = []
    next_cursor: str | None = None


class ThreadSearchMatch(WireModel):
    snippet: str
    thread: ThreadInfo


class ThreadSearchResult(WireModel):
    data: list[ThreadSearchMatch] = []
    next_cursor: str | None = None
    backwards_cursor: str | None = None


class ThreadGoalResult(WireModel):
    goal: ThreadGoal


class ThreadGoalGetResult(WireModel):
    goal: ThreadGoal | None = None


# --- turn/* + inject_items params ---------------------------------------------


class TurnStartParams(WireModel):
    thread_id: str
    input: list[TextInput]
    cwd: str
    approval_policy: ApprovalPolicy | None = None
    approvals_reviewer: ApprovalsReviewer | None = None
    sandbox_policy: SandboxPolicy | None = None
    effort: Effort | None = None
    summary: ReasoningSummary | None = None
    model: str | None = None
    service_tier: str | None = None
    output_schema: dict[str, object] | None = None
    personality: Personality | None = None


class TurnSteerParams(WireModel):
    thread_id: str
    expected_turn_id: str
    input: list[TextInput]


class TurnInterruptParams(WireModel):
    thread_id: str
    turn_id: str


class InjectItemsParams(WireModel):
    thread_id: str
    items: list[dict[str, object]]
