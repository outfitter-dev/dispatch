"""Input/output models for the v1 ops. Field descriptions project to CLI help
and MCP ``inputSchema`` descriptions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from outfitter.dispatch.client.models import (
    ApprovalPolicy,
    ApprovalsReviewer,
    Effort,
    Personality,
    ReasoningSummary,
    ThreadGoalStatus,
    ThreadSandbox,
)
from outfitter.dispatch.registry.models import (
    LaneSource,
    LaneStatus,
    ServiceTierSource,
    SyncState,
    TurnRuntimeStatus,
)

# --- inputs -------------------------------------------------------------------

SendMode = Literal["send", "steer", "queue", "interject", "context"]
ThreadActionSource = Literal["own", "attached", "unmanaged"]
SearchSortKey = Literal["created_at", "updated_at"]
SearchDateField = Literal["created_at", "updated_at"]
THREAD_SELECTOR_DESCRIPTION = (
    "Thread selector: dispatch ref, full Codex thread id, or unique handle/title."
)
EXISTING_THREAD_SELECTOR_DESCRIPTION = (
    "Existing thread selector: dispatch ref, full Codex thread id, or unique handle/title."
)
SOURCE_THREAD_SELECTOR_DESCRIPTION = (
    "Source thread selector: dispatch ref, full Codex thread id, or unique handle/title."
)
TARGET_THREAD_SELECTOR_DESCRIPTION = (
    "Target thread selector: dispatch ref, full Codex thread id, or unique handle/title."
)


class OpenInput(BaseModel):
    name: str = Field(description="Thread title; also seeds the mutable @handle.")
    cwd: str = Field(default=".", description="Working directory for the managed thread.")


class NewInput(BaseModel):
    name: str = Field(description="Thread title; prefix/presets may decorate it.")
    preset: list[str] = Field(
        default_factory=list, description="Preset(s) to apply, left to right."
    )
    goal: str | None = Field(
        default=None,
        description="Native App Server goal objective to create before the initial message.",
    )
    text: str | None = Field(default=None, description="Initial message text.")
    send: bool = Field(default=True, description="Send the initial message when text is present.")
    cwd: str | None = Field(default=None, description="Working directory for config discovery.")
    prefix: str | None = Field(default=None, description="Name prefix template.")
    sandbox: ThreadSandbox | None = Field(default=None, description="Thread sandbox mode.")
    approval_policy: ApprovalPolicy | None = Field(default=None, description="Approval policy.")
    approvals_reviewer: ApprovalsReviewer | None = Field(
        default=None, description="Where approval requests are routed."
    )
    model: str | None = Field(default=None, description="Model override.")
    model_provider: str | None = Field(default=None, description="Model provider override.")
    effort: Effort | None = Field(default=None, description="Initial turn reasoning effort.")
    summary: ReasoningSummary | None = Field(default=None, description="Reasoning summary mode.")
    personality: Personality | None = Field(default=None, description="Personality override.")
    service_tier: str | None = Field(default=None, description="Service tier override.")
    ephemeral: bool | None = Field(default=None, description="Create an ephemeral thread.")
    base_instructions: str | None = Field(default=None, description="Base instructions text.")
    base_file: str | None = Field(default=None, description="Base instructions file.")
    developer_instructions: str | None = Field(
        default=None, description="Developer instructions text."
    )
    developer_file: str | None = Field(default=None, description="Developer instructions file.")


class AttachInput(BaseModel):
    thread: str = Field(description="Full Codex thread id of an existing thread.")
    sync: bool = Field(default=False, description="Run a quick sync after attaching.")


class LaneTextInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)
    text: str = Field(description="Message text.")


class SendInput(LaneTextInput):
    mode: SendMode = Field(
        default="send",
        description=(
            "Delivery mode: send starts a turn, steer adds to an active turn, "
            "queue stores durable delivery for the next idle transition, "
            "interject cancels then starts a replacement turn, context injects "
            "model-visible context."
        ),
    )
    intro: bool = Field(
        default=False,
        description="Prepend a dispatch reply hint derived from the current CODEX_THREAD_ID.",
    )
    caller_thread_id: str | None = Field(
        default=None,
        exclude=True,
        json_schema_extra={"x-dispatch-internal": True},
    )


class LaneInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)


class ThreadTargetInput(BaseModel):
    target: str = Field(description=THREAD_SELECTOR_DESCRIPTION)


class LaneSyncInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)
    full: bool = Field(default=False, description="Scan the full source file instead of top+tail.")


class LaneRenameInput(BaseModel):
    old: str = Field(description=EXISTING_THREAD_SELECTOR_DESCRIPTION)
    new: str = Field(description="New mutable thread title/handle; @ is optional.")


class SearchInput(BaseModel):
    query: str = Field(description="Substring/full-text query for Codex thread search.")
    lane: str | None = Field(default=None, description="Limit search to one thread selector.")
    directory: str | None = Field(
        default=None, description="Only include threads whose cwd is inside this directory."
    )
    repo: str | None = Field(
        default=None, description="Only include threads whose cwd is inside this repo root."
    )
    managed: bool = Field(default=False, description="Only include dispatch-managed threads.")
    unmanaged: bool = Field(default=False, description="Only include unmanaged Codex threads.")
    archived: bool = Field(
        default=False, description="Search archived threads instead of active ones."
    )
    since: str | None = Field(default=None, description="Inclusive ISO date/time lower bound.")
    until: str | None = Field(default=None, description="Inclusive ISO date/time upper bound.")
    date_field: SearchDateField = Field(
        default="updated_at", description="Timestamp field used for since/until filtering."
    )
    sort: SearchSortKey = Field(default="updated_at", description="App Server search sort key.")
    ascending: bool = Field(default=False, description="Sort oldest first.")
    limit: int = Field(default=20, ge=1, description="Max matches to return.")
    max_scan: int = Field(
        default=200,
        ge=1,
        description="Max App Server matches to scan while applying dispatch-side filters.",
    )


class ShowInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)
    include_transcript: bool = Field(
        default=False, description="Include a compact transcript from thread/read."
    )
    max_items: int = Field(default=20, ge=1, description="Max transcript items to return.")


class WatchInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)
    limit: int = Field(default=20, ge=1, description="Max live App Server events to collect.")
    timeout: float = Field(
        default=10.0,
        ge=0.0,
        le=25.0,
        description="Seconds to wait for live events before returning.",
    )


class TranscriptInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)
    limit: int = Field(default=50, ge=1, description="Max compact transcript items to return.")


class GoalGetInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)


class GoalSetInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)
    objective: str | None = Field(default=None, description="Goal objective text.")
    status: ThreadGoalStatus | None = Field(default=None, description="Goal status.")
    token_budget: int | None = Field(default=None, ge=1, description="Optional token budget.")


class GoalClearInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)


class ForkInput(BaseModel):
    lane: str = Field(description=SOURCE_THREAD_SELECTOR_DESCRIPTION)
    name: str = Field(description="Name for the new forked lane.")
    cwd: str | None = Field(default=None, description="Working directory override for the fork.")
    sandbox: ThreadSandbox | None = Field(
        default=None, description="Sandbox override for the fork."
    )
    approval_policy: ApprovalPolicy | None = Field(
        default=None, description="Approval policy override for the fork."
    )
    approvals_reviewer: ApprovalsReviewer | None = Field(
        default=None, description="Approval reviewer override for the fork."
    )
    model: str | None = Field(default=None, description="Model override for the fork.")
    model_provider: str | None = Field(default=None, description="Model provider override.")
    base_instructions: str | None = Field(default=None, description="Base instructions override.")
    developer_instructions: str | None = Field(
        default=None, description="Developer instructions override."
    )
    service_tier: str | None = Field(default=None, description="Service tier override.")
    ephemeral: bool = Field(default=False, description="Create an ephemeral fork.")


class RollbackInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)
    turns: int = Field(default=1, ge=1, description="Turns to drop from the end of history.")


class CompactInput(BaseModel):
    lane: str = Field(description=THREAD_SELECTOR_DESCRIPTION)


class RosterInput(BaseModel):
    include_archived: bool = Field(default=False, description="Include archived lanes.")


class DiscoverInput(BaseModel):
    limit: int = Field(default=50, ge=1, description="Max persisted Codex sessions to list.")


class ModelsInput(BaseModel):
    refresh: bool = Field(
        default=True, description="Refresh from the Codex App Server before returning the catalog."
    )
    include_hidden: bool = Field(default=False, description="Include hidden model catalog rows.")


# --- outputs ------------------------------------------------------------------


class LaneRef(BaseModel):
    ref: str
    id: str
    handle: str
    source: LaneSource
    status: LaneStatus
    cwd: str | None = None


class ThreadActionRef(BaseModel):
    ref: str | None = None
    id: str
    handle: str | None = None
    managed: bool
    source: ThreadActionSource
    status: str | None = None
    cwd: str | None = None


class ManagedThreadIdentity(BaseModel):
    """Stable identity fields for outputs that refer to one managed thread.

    ``lane`` stays as the compatibility field for the full Codex thread id.
    ``id`` names the same full id explicitly for thread-oriented consumers.
    """

    lane: str
    ref: str
    id: str
    title: str | None = None
    handle: str | None = None
    managed: bool = True
    source: LaneSource
    status: LaneStatus
    cwd: str | None = None


class LaneSyncView(BaseModel):
    state: SyncState = "unknown"
    last_synced_at: str | None = None
    source_path: str | None = None
    source_size: int | None = None
    latest_event_at: str | None = None
    latest_turn_id: str | None = None
    transcript_partial: bool = True
    error: str | None = None


class LatestTurnView(BaseModel):
    id: str | None = Field(default=None, description="Latest observed App Server turn id.")
    status: TurnRuntimeStatus | None = Field(
        default=None, description="Latest observed turn runtime status."
    )
    error: str | None = Field(default=None, description="Latest App Server turn error text.")
    error_at: str | None = Field(default=None, description="When the latest turn error occurred.")


class ServiceTierView(BaseModel):
    requested: str | None = None
    resolved: str | None = None
    name: str | None = None
    source: ServiceTierSource = "unknown"


class ThreadModelView(BaseModel):
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    service_tier: ServiceTierView = Field(default_factory=ServiceTierView)


class LaneListItem(LaneRef):
    sync: LaneSyncView = Field(default_factory=LaneSyncView)
    latest_turn: LatestTurnView = Field(default_factory=LatestTurnView)
    model: ThreadModelView = Field(default_factory=ThreadModelView)


class NewLane(LaneRef):
    message_accepted: bool = Field(
        description="Whether the App Server accepted the initial message request."
    )
    goal_set: bool = Field(
        default=False, description="Whether a native App Server goal was set before launch."
    )
    latest_turn: LatestTurnView = Field(default_factory=LatestTurnView)
    model: ThreadModelView = Field(default_factory=ThreadModelView)


class LaneDetail(LaneRef):
    active_turn_id: str | None = None
    latest_turn: LatestTurnView = Field(default_factory=LatestTurnView)
    sync: LaneSyncView = Field(default_factory=LaneSyncView)
    model: ThreadModelView = Field(default_factory=ThreadModelView)
    transcript: list[TranscriptItem] = Field(default_factory=list)


class TranscriptItem(BaseModel):
    turn_id: str | None = None
    item_id: str | None = None
    type: str
    text: str | None = None


class WatchEvent(BaseModel):
    method: str
    params: dict[str, object] = Field(default_factory=dict)
    request_id: int | None = None


class WatchOutput(ManagedThreadIdentity):
    events: list[WatchEvent]
    timed_out: bool


class TranscriptOutput(ManagedThreadIdentity):
    items: list[TranscriptItem]


class Goal(BaseModel):
    thread_id: str
    objective: str
    status: ThreadGoalStatus
    tokens_used: int
    time_used_seconds: int
    created_at: int
    updated_at: int
    token_budget: int | None = None


class GoalView(ManagedThreadIdentity):
    goal: Goal | None = None


class ActionAck(ManagedThreadIdentity):
    op: str
    accepted: bool = True
    detail: str | None = None


class LaneSyncResult(ManagedThreadIdentity):
    sync: LaneSyncView
    model: ThreadModelView = Field(default_factory=ThreadModelView)


class Roster(BaseModel):
    lanes: list[LaneListItem]


class DiscoveredSession(BaseModel):
    """A persisted Codex session discoverable via ``thread/list`` — a candidate to
    ``attach``, not (yet) a managed lane. Mirrors the available ``ThreadInfo`` subset."""

    id: str
    name: str | None = None
    preview: str | None = None
    cwd: str | None = None
    status: str | None = None
    source: str | None = None
    ephemeral: bool | None = None
    model: ThreadModelView = Field(default_factory=ThreadModelView)


class Discovery(BaseModel):
    sessions: list[DiscoveredSession]


class SearchMatch(BaseModel):
    ref: str | None = None
    id: str
    handle: str | None = None
    managed: bool
    source: ThreadActionSource
    status: str | None = None
    name: str | None = None
    cwd: str | None = None
    preview: str | None = None
    snippet: str
    created_at: int | None = None
    updated_at: int | None = None


class SearchOutput(BaseModel):
    query: str
    matches: list[SearchMatch]
    scanned: int
    next_cursor: str | None = None
    experimental: bool = True


class ModelServiceTierView(BaseModel):
    id: str
    name: str
    description: str


class ModelCatalogItem(BaseModel):
    id: str
    provider: str
    display_name: str | None = None
    description: str | None = None
    is_default: bool | None = None
    hidden: bool | None = None
    default_reasoning_effort: str | None = None
    supported_reasoning_efforts: list[str] = Field(default_factory=list)
    default_service_tier: str | None = None
    service_tiers: list[ModelServiceTierView] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)
    last_seen_at: str


class ModelConfigView(BaseModel):
    model: str | None = None
    model_provider: str | None = None
    service_tier: str | None = None
    model_reasoning_effort: str | None = None


class ModelCatalogOutput(BaseModel):
    refreshed_at: str | None = None
    source: str
    configured_default: ModelConfigView
    models: list[ModelCatalogItem]


# --- trigger ops --------------------------------------------------------------

TriggerWhenKind = Literal["interval", "cron", "idle_for", "turn_completed", "waiting_on_approval"]
TriggerActionKind = Literal["send", "steer", "brief"]


class TriggerAddInput(BaseModel):
    name: str = Field(description="Trigger name.")
    lane: str = Field(description=TARGET_THREAD_SELECTOR_DESCRIPTION)
    when: TriggerWhenKind = Field(description="Trigger condition.")
    action: TriggerActionKind = Field(description="Action to run when it fires.")
    text: str = Field(description="Action text (prompt or injected context).")
    seconds: float | None = Field(default=None, description="Seconds for interval/idle_for.")
    cron: str | None = Field(default=None, description="Cron expression (croniter) for cron.")
    idle_only: bool = Field(default=False, description="Only fire when the lane is idle.")
    min_interval: float | None = Field(default=None, description="Min seconds between firings.")
    dedupe: bool = Field(default=False, description="Suppress identical consecutive firings.")


class TriggerIdInput(BaseModel):
    id: str = Field(description="Trigger id.")


class TriggerListInput(BaseModel):
    pass


class TriggerView(BaseModel):
    id: str
    name: str
    lane: str
    when: str
    action: str
    enabled: bool


class TriggerList(BaseModel):
    triggers: list[TriggerView]


class TriggerRemoved(BaseModel):
    id: str
    removed: bool


# --- lifecycle ops (status / log) ---------------------------------------------


class StatusInput(BaseModel):
    pass


class StatusOutput(BaseModel):
    lanes: int
    idle: int
    busy: int
    triggers: int
    triggers_enabled: int


class LogInput(BaseModel):
    limit: int = Field(default=20, description="How many recent actions to show.")


class ActionView(BaseModel):
    ts: str
    op: str
    lane: str | None = None
    trigger_id: str | None = None
    outcome: str
    detail: str | None = None


class LogOutput(BaseModel):
    actions: list[ActionView]
