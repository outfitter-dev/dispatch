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

from typing import Annotated, Any, Literal, TypeGuard, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic.alias_generators import to_camel

ThreadSandbox = Literal["read-only", "workspace-write", "danger-full-access"]
ApprovalPolicy = Literal["never", "untrusted", "on-request"]
ApprovalsReviewer = Literal["user", "auto_review", "guardian_subagent"]
SandboxPolicyType = Literal["readOnly", "workspaceWrite", "externalSandbox", "dangerFullAccess"]
# App Server 0.144 advertises model-defined efforts rather than a closed enum.
Effort = str
ReasoningSummary = Literal["auto", "concise", "detailed", "none"]
Personality = Literal["none", "friendly", "pragmatic"]
Decision = Literal["accept", "acceptForSession", "decline", "cancel"]
type JsonRpcId = int | str
type ServerRequestCategory = Literal[
    "approval",
    "auth",
    "attestation",
    "tool_call",
    "user_input",
    "elicitation",
    "unknown",
]
SortDirection = Literal["asc", "desc"]
TurnItemsView = Literal["notLoaded", "summary", "full"]
TurnStatus = Literal["completed", "interrupted", "failed", "inProgress"]
ThreadSortKey = Literal["created_at", "updated_at", "recency_at"]
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


# --- account / capacity ------------------------------------------------------


class AccountInfo(WireModel):
    type: str
    email: str | None = None
    plan_type: str | None = None
    credential_source: str | None = None


class AccountReadResult(WireModel):
    account: AccountInfo | None = None
    requires_openai_auth: bool


class RateLimitWindow(WireModel):
    used_percent: int
    window_duration_mins: int | None = None
    resets_at: int | None = None


class CreditsSnapshot(WireModel):
    has_credits: bool
    unlimited: bool
    balance: str | None = None


class SpendControlLimitSnapshot(WireModel):
    limit: str
    used: str
    remaining_percent: int
    resets_at: int


class RateLimitSnapshot(WireModel):
    limit_id: str | None = None
    limit_name: str | None = None
    plan_type: str | None = None
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    credits: CreditsSnapshot | None = None
    individual_limit: SpendControlLimitSnapshot | None = None
    rate_limit_reached_type: str | None = None


class RateLimitResetCredit(WireModel):
    id: str
    reset_type: str
    status: str
    granted_at: int
    expires_at: int | None = None
    title: str | None = None
    description: str | None = None


class RateLimitResetCreditsSummary(WireModel):
    available_count: int
    credits: list[RateLimitResetCredit] | None = None


class AccountRateLimitsResult(WireModel):
    rate_limits: RateLimitSnapshot
    rate_limits_by_limit_id: dict[str, RateLimitSnapshot] | None = None
    rate_limit_reset_credits: RateLimitResetCreditsSummary | None = None


class AccountUsageSummary(WireModel):
    lifetime_tokens: int | None = None
    current_streak_days: int | None = None
    longest_streak_days: int | None = None
    peak_daily_tokens: int | None = None
    longest_running_turn_sec: int | None = None


class AccountUsageDailyBucket(WireModel):
    start_date: str
    tokens: int


class AccountUsageResult(WireModel):
    summary: AccountUsageSummary
    daily_usage_buckets: list[AccountUsageDailyBucket] | None = None


def is_json_rpc_id(value: object) -> TypeGuard[JsonRpcId]:
    """Return whether ``value`` is a JSON-RPC request/response id.

    ``bool`` is an ``int`` subclass in Python but is not a supported protocol id.
    """

    return type(value) in (int, str)


class JsonRpcError(WireModel):
    """A JSON-RPC error envelope sent in response to a server request."""

    code: int
    message: str
    data: object | None = None


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
    input_modalities: list[str] = Field(default_factory=list)
    supports_personality: bool | None = None
    upgrade: str | None = None

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


class ModelListParams(WireModel):
    cursor: str | None = None
    include_hidden: bool | None = None
    limit: int | None = None


class PermissionProfileSummary(WireModel):
    id: str
    description: str | None = None
    allowed: bool


class PermissionProfileListResult(WireModel):
    data: list[PermissionProfileSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class PermissionProfileListParams(WireModel):
    cursor: str | None = None
    cwd: str | None = None
    limit: int | None = Field(default=None, ge=0)


# --- shared shapes ------------------------------------------------------------


class TextInput(WireModel):
    type: Literal["text"] = "text"
    text: str


ImageDetail = Literal["auto", "low", "high", "original"]


class ImageInput(WireModel):
    type: Literal["image"] = "image"
    url: str
    detail: ImageDetail | None = None


class LocalImageInput(WireModel):
    type: Literal["localImage"] = "localImage"
    path: str
    detail: ImageDetail | None = None


type UserInput = Annotated[
    TextInput | ImageInput | LocalImageInput,
    Field(discriminator="type"),
]


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
    agent_nickname: str | None = None
    agent_role: str | None = None
    cli_version: str | None = None
    ephemeral: bool | None = None
    status: ThreadStatus | None = None
    cwd: str | None = None
    name: str | None = None
    path: str | None = None
    preview: str | None = None
    source: JsonValue = None
    thread_source: str | None = None
    model_provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    recency_at: int | None = None
    turns: list[dict[str, object]] = Field(default_factory=list)

    @property
    def source_kind(self) -> str | None:
        """Return the App Server sourceKinds-compatible classification."""

        if isinstance(self.source, str):
            return self.source
        if not isinstance(self.source, dict):
            return None
        if "custom" in self.source:
            return "unknown"
        subagent = self.source.get("subAgent")
        if subagent == "review":
            return "subAgentReview"
        if isinstance(subagent, str) and subagent in {"compact", "memory_consolidation"}:
            return "subAgentCompact"
        if isinstance(subagent, dict) and "thread_spawn" in subagent:
            return "subAgentThreadSpawn"
        if subagent is not None:
            return "subAgentOther"
        return "unknown"

    @property
    def spawned_source(self) -> dict[str, object] | None:
        if not isinstance(self.source, dict):
            return None
        subagent = self.source.get("subAgent")
        if not isinstance(subagent, dict):
            return None
        spawned = subagent.get("thread_spawn")
        return cast(dict[str, object], spawned) if isinstance(spawned, dict) else None


# --- thread/* params + results ------------------------------------------------


class ThreadStartParams(WireModel):
    cwd: str | None = None
    permissions: str | None = None
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

    @model_validator(mode="after")
    def _permissions_excludes_sandbox(self) -> ThreadStartParams:
        if self.permissions is not None and self.sandbox is not None:
            raise ValueError("permissions cannot be combined with sandbox")
        return self


class ThreadResumeParams(WireModel):
    thread_id: str
    permissions: str | None = None
    exclude_turns: bool | None = None
    initial_turns_page: ThreadResumeInitialTurnsPageParams | None = None


class ThreadResumeInitialTurnsPageParams(WireModel):
    limit: int | None = Field(default=None, ge=0)
    sort_direction: SortDirection | None = None
    items_view: TurnItemsView | None = None


class ThreadForkParams(WireModel):
    thread_id: str
    cwd: str | None = None
    permissions: str | None = None
    sandbox: ThreadSandbox | None = None
    approval_policy: ApprovalPolicy | None = None
    approvals_reviewer: ApprovalsReviewer | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    service_tier: str | None = None
    model: str | None = None
    model_provider: str | None = None
    last_turn_id: str | None = None
    ephemeral: bool = False

    @model_validator(mode="after")
    def _permissions_excludes_sandbox(self) -> ThreadForkParams:
        if self.permissions is not None and self.sandbox is not None:
            raise ValueError("permissions cannot be combined with sandbox")
        return self


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
    parent_thread_id: str | None = None
    ancestor_thread_id: str | None = None
    use_state_db_only: bool | None = None

    @model_validator(mode="after")
    def _exclusive_topology_filters(self) -> ThreadListParams:
        if self.parent_thread_id is not None and self.ancestor_thread_id is not None:
            raise ValueError("parent_thread_id and ancestor_thread_id are mutually exclusive")
        return self


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
    backwards_cursor: str | None = None


class TurnError(WireModel):
    message: str
    additional_details: str | None = None
    codex_error_info: JsonValue = None


class ThreadTurn(WireModel):
    id: str
    status: TurnStatus
    items: list[dict[str, object]] = Field(default_factory=list)
    items_view: TurnItemsView = "full"
    started_at: int | None = None
    completed_at: int | None = None
    duration_ms: int | None = None
    error: TurnError | None = None


class ThreadTurnsListParams(WireModel):
    thread_id: str
    cursor: str | None = None
    limit: int | None = Field(default=None, ge=0)
    sort_direction: SortDirection | None = None
    items_view: TurnItemsView | None = None


class ThreadTurnsPage(WireModel):
    data: list[ThreadTurn] = Field(default_factory=list)
    next_cursor: str | None = None
    backwards_cursor: str | None = None


class ThreadItemsListParams(WireModel):
    thread_id: str
    cursor: str | None = None
    limit: int | None = Field(default=None, ge=0)
    sort_direction: SortDirection | None = None
    turn_id: str | None = None


class ThreadItemsPage(WireModel):
    data: list[dict[str, object]] = Field(default_factory=list)
    next_cursor: str | None = None
    backwards_cursor: str | None = None


class ActivePermissionProfile(WireModel):
    id: str
    extends: str | None = None


class ThreadResumeResult(WireModel):
    """Full ``thread/resume`` response, including an optional bootstrap page.

    Configuration fields remain optional so callers can also parse responses
    from App Server versions that returned only ``thread``.
    """

    thread: ThreadInfo
    initial_turns_page: ThreadTurnsPage | None = None
    active_permission_profile: ActivePermissionProfile | None = None
    approval_policy: JsonValue = None
    approvals_reviewer: ApprovalsReviewer | None = None
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    reasoning_effort: str | None = None
    runtime_workspace_roots: list[str] = Field(default_factory=list)
    sandbox: JsonValue = None
    service_tier: str | None = None
    instruction_sources: list[str] = Field(default_factory=list)


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
    input: list[UserInput]
    cwd: str
    permissions: str | None = None
    approval_policy: ApprovalPolicy | None = None
    approvals_reviewer: ApprovalsReviewer | None = None
    sandbox_policy: SandboxPolicy | None = None
    effort: Effort | None = Field(default=None, min_length=1)
    summary: ReasoningSummary | None = None
    model: str | None = None
    service_tier: str | None = None
    output_schema: dict[str, object] | None = None
    personality: Personality | None = None

    @model_validator(mode="after")
    def _permissions_excludes_sandbox(self) -> TurnStartParams:
        if self.permissions is not None and self.sandbox_policy is not None:
            raise ValueError("permissions cannot be combined with sandboxPolicy")
        return self


class TurnSteerParams(WireModel):
    thread_id: str
    expected_turn_id: str
    input: list[UserInput]


class TurnInterruptParams(WireModel):
    thread_id: str
    turn_id: str


class InjectItemsParams(WireModel):
    thread_id: str
    items: list[dict[str, object]]
