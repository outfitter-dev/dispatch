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
    InboxMessageKind,
    InboxMessageState,
    LaneSource,
    LaneStatus,
    ServiceTierSource,
    SubscriptionAckPolicy,
    SubscriptionDeliverPolicy,
    SubscriptionDelivery,
    SubscriptionState,
    SubscriptionWhen,
    SyncState,
    TurnRuntimeStatus,
)

# --- inputs -------------------------------------------------------------------

SendMode = Literal["send", "steer", "queue", "interject", "context"]
ThreadActionSource = Literal["own", "attached", "unmanaged"]
SearchSortKey = Literal["created_at", "updated_at"]
SearchDateField = Literal["created_at", "updated_at"]
HistoryView = Literal["auto", "overview", "summary", "items", "tools", "files"]
WorkspaceSetupMode = Literal["auto", "skip", "run"]
WorktreeMode = Literal["none", "create"]
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
    packet: str | None = Field(
        default=None,
        description="Launch packet directory (goal.md/prompt.md/output.schema.json/...).",
    )
    input_file: str | None = Field(
        default=None, description="Initial message text file (use - for stdin)."
    )
    goal_file: str | None = Field(
        default=None, description="Native goal objective file (use - for stdin)."
    )
    output_schema_file: str | None = Field(
        default=None, description="JSON Schema file for structured turn output."
    )
    stage: str | None = Field(
        default=None,
        description=(
            "Stage packet parts to .agents/sessions/<ref>/: 'all' or a comma list "
            "(config,goal,prompt,output_schema,base,developer,hooks,codex_config)."
        ),
    )
    inline: str | None = Field(
        default=None,
        description="Packet parts to keep inline only (not staged): 'all' or a comma list.",
    )
    workspace: str | None = Field(
        default=None,
        description="Workspace preflight mode: none, auto, or a named workspace preset.",
    )
    workspace_setup: WorkspaceSetupMode = Field(
        default="auto",
        description="Workspace setup execution: auto, skip, or run.",
    )
    worktree: WorktreeMode | None = Field(
        default=None,
        description=("Git worktree preflight: none or create; omit to use workspace config."),
    )
    worktree_path: str | None = Field(
        default=None, description="Explicit path for a Dispatch-created git worktree."
    )
    worktree_branch: str | None = Field(
        default=None, description="Branch to check out/create in a Dispatch-created worktree."
    )
    worktree_base: str | None = Field(
        default=None, description="Base ref for new Dispatch-created worktree branches."
    )
    subscribe: str | None = Field(
        default=None,
        description=(
            "Create a subscription to the new lane. Use true/default for defaults or "
            "spec keys like when:done,delivery:inbox,to:<ref>."
        ),
    )
    caller_thread_id: str | None = Field(
        default=None,
        exclude=True,
        json_schema_extra={"x-dispatch-internal": True},
    )


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


class HistoryInput(BaseModel):
    lane: str | None = Field(
        default=None,
        description="Optional thread selector. Omit for managed-thread overview.",
    )
    view: HistoryView = Field(
        default="auto",
        description="History view: auto, overview, summary, items, tools, or files.",
    )
    item_type: str | None = Field(default=None, description="Only include matching item types.")
    tool: str | None = Field(default=None, description="Only include matching tool names.")
    grep: str | None = Field(default=None, description="Only include items containing text.")
    cwd: str | None = Field(
        default=None, description="Only include threads whose cwd contains text."
    )
    source: LaneSource | None = Field(default=None, description="Only include threads by source.")
    status: LaneStatus | None = Field(default=None, description="Only include threads by status.")
    has_tool: str | None = Field(
        default=None, description="Only include summaries using this tool."
    )
    changed: bool | None = Field(
        default=None,
        description=(
            "Only include summaries with changed workspace files when true, clean when false."
        ),
    )
    min_bytes: int | None = Field(
        default=None, ge=0, description="Only include summaries with at least this transcript size."
    )
    raw: bool = Field(default=False, description="Include raw App Server item payloads.")
    limit: int = Field(default=50, ge=1, description="Max rows/items to return.")


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


class InboxListInput(BaseModel):
    lane: str | None = Field(
        default=None, description="Recipient thread selector. Omit to use current lane when known."
    )
    state: InboxMessageState | None = Field(
        default="pending", description="Inbox state filter; omit for all states."
    )
    kind: InboxMessageKind | None = Field(default=None, description="Inbox message kind filter.")
    limit: int = Field(default=50, ge=1, description="Max inbox messages to return.")
    caller_thread_id: str | None = Field(
        default=None,
        exclude=True,
        json_schema_extra={"x-dispatch-internal": True},
    )


class InboxReadInput(BaseModel):
    id: int = Field(description="Inbox message id.")


class InboxAckInput(BaseModel):
    id: int | None = Field(default=None, description="Inbox message id.")
    lane: str | None = Field(default=None, description="Ack all pending messages for this lane.")
    all: bool = Field(default=False, description="Ack all pending messages for the selected lane.")
    caller_thread_id: str | None = Field(
        default=None,
        exclude=True,
        json_schema_extra={"x-dispatch-internal": True},
    )


class SubscribeInput(BaseModel):
    target: str = Field(description=TARGET_THREAD_SELECTOR_DESCRIPTION)
    spec: str | None = Field(
        default=None,
        description="Compact spec: when:done,to:self,delivery:turn,deliver:idle,tail:1.",
    )
    when: SubscriptionWhen | None = Field(default=None, description="Subscription condition.")
    to: str | None = Field(default=None, description="Subscriber thread selector or self.")
    delivery: SubscriptionDelivery | None = Field(default=None, description="Delivery mode.")
    deliver: SubscriptionDeliverPolicy | None = Field(
        default=None, description="Subscriber-side delivery policy."
    )
    tail: int | None = Field(default=None, ge=0, description="Final messages to include.")
    once: bool | None = Field(default=None, description="Auto-complete after first match.")
    ack: SubscriptionAckPolicy | None = Field(default=None, description="Acknowledgement policy.")
    caller_thread_id: str | None = Field(
        default=None,
        exclude=True,
        json_schema_extra={"x-dispatch-internal": True},
    )


class SubscriptionListInput(BaseModel):
    target: str | None = Field(default=None, description="Filter by target thread selector.")
    subscriber: str | None = Field(
        default=None, description="Filter by subscriber thread selector."
    )
    state: SubscriptionState | None = Field(default=None, description="Filter by state.")


class SubscriptionIdInput(BaseModel):
    id: str = Field(description="Subscription id.")


# --- outputs ------------------------------------------------------------------


class LaneCapabilities(BaseModel):
    read: bool = True
    sync: bool = True
    tail: bool = True
    send: bool
    context: bool
    steer: bool
    queue: bool
    interject: bool
    goal_set: bool
    goal_clear: bool
    stop: bool
    fork: bool
    rollback: bool
    compact: bool


class LaneRef(BaseModel):
    ref: str
    id: str
    handle: str
    source: LaneSource
    status: LaneStatus
    cwd: str | None = None
    writable: bool = Field(description="Whether turn-writing commands are allowed.")
    capabilities: LaneCapabilities = Field(description="Current authority capabilities.")
    write_locked_reason: str | None = Field(
        default=None, description="Why turn-writing commands are blocked, if blocked."
    )


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
    writable: bool = Field(description="Whether turn-writing commands are allowed.")
    capabilities: LaneCapabilities = Field(description="Current authority capabilities.")
    write_locked_reason: str | None = Field(
        default=None, description="Why turn-writing commands are blocked, if blocked."
    )


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


class InboxMessageView(BaseModel):
    id: int
    recipient_ref: str | None = None
    recipient_lane: str
    source_ref: str | None = None
    source_lane: str | None = None
    subscription_id: str | None = None
    kind: InboxMessageKind
    subject: str
    body: str
    payload: dict[str, object] = Field(default_factory=dict)
    state: InboxMessageState
    delivery: SubscriptionDelivery
    queued_message_id: int | None = None
    created_at: str
    delivered_at: str | None = None
    acked_at: str | None = None


class InboxList(BaseModel):
    messages: list[InboxMessageView] = Field(default_factory=list)


class InboxAckResult(BaseModel):
    acked: int
    message: InboxMessageView | None = None


class SubscriptionView(BaseModel):
    id: str
    target_ref: str
    target_lane: str
    subscriber_ref: str
    subscriber_lane: str
    when: SubscriptionWhen
    delivery: SubscriptionDelivery
    deliver: SubscriptionDeliverPolicy
    tail: int
    once: bool
    ack: SubscriptionAckPolicy
    state: SubscriptionState
    created_at: str
    updated_at: str
    last_matched_at: str | None = None
    last_inbox_message_id: int | None = None


class SubscriptionList(BaseModel):
    subscriptions: list[SubscriptionView] = Field(default_factory=list)


class SubscriptionRemoved(BaseModel):
    id: str
    removed: bool


StagePart = Literal[
    "config", "goal", "prompt", "output_schema", "base", "developer", "hooks", "codex_config"
]


class StagedFile(BaseModel):
    part: StagePart = Field(description="Which packet part this entry stages.")
    path: str = Field(description="Path written, relative to the session directory.")
    bytes: int | None = Field(default=None, description="Byte length for file parts.")
    sha256: str | None = Field(default=None, description="SHA-256 hex digest for file parts.")


class StageView(BaseModel):
    """What a launch staged (or, in a plan, would stage) under .agents/sessions/<ref>/."""

    parts: list[StagePart] = Field(
        default_factory=list, description="Packet parts staged to disk, in canonical order."
    )
    session_dir: str | None = Field(
        default=None, description="Absolute session directory (known only after launch)."
    )
    files: list[StagedFile] = Field(
        default_factory=list, description="Files/dirs written under the session directory."
    )


class NewLane(LaneRef):
    message_accepted: bool = Field(
        description="Whether the App Server accepted the initial message request."
    )
    goal_set: bool = Field(
        default=False, description="Whether a native App Server goal was set before launch."
    )
    staged: StageView = Field(
        default_factory=StageView, description="Packet parts staged to the session directory."
    )
    workspace: WorkspaceView = Field(description="Workspace preflight result.")
    latest_turn: LatestTurnView = Field(default_factory=LatestTurnView)
    model: ThreadModelView = Field(default_factory=ThreadModelView)
    subscription: SubscriptionView | None = None


LaunchSlot = Literal["goal", "prompt", "output_schema", "base", "developer"]
# stdin is read CLI-side and inlined before the daemon resolves a launch, so the
# daemon reports it as "inline"; no separate "stdin" origin reaches resolution.
LaunchOrigin = Literal["inline", "file", "packet", "config"]


class LaunchInputSource(BaseModel):
    slot: LaunchSlot = Field(description="Which launch input this source fills.")
    origin: LaunchOrigin = Field(description="Where the resolved content came from.")
    path: str | None = Field(default=None, description="Absolute source file path, if any.")
    bytes: int = Field(description="UTF-8 byte length of the resolved content.")
    sha256: str = Field(description="SHA-256 hex digest of the resolved content.")


class LaunchSettingsView(BaseModel):
    """Effective lane settings a launch would use (no secrets, no mutation)."""

    sandbox: str | None = None
    approval_policy: str | None = None
    approvals_reviewer: str | None = None
    model: str | None = None
    model_provider: str | None = None
    effort: str | None = None
    summary: str | None = None
    personality: str | None = None
    service_tier: str | None = None
    ephemeral: bool = False


class WorkspaceEnvironmentView(BaseModel):
    version: int | None = None
    name: str | None = None
    setup_script: str | None = None
    cleanup_script: str | None = None
    unknown_keys: list[str] = Field(default_factory=list)


class WorkspaceSetupView(BaseModel):
    policy: str = Field(description="Setup policy decision.")
    ran: bool = Field(description="Whether the setup script ran.")
    script: str | None = None
    cwd: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None


class WorktreeView(BaseModel):
    mode: WorktreeMode = "none"
    state: str = Field(description="Worktree preflight state.")
    path: str | None = None
    branch: str | None = None
    base: str | None = None
    head: str | None = None
    source_repo: str | None = None
    created: bool = False


class WorkspaceView(BaseModel):
    mode: str = Field(description="Requested workspace mode or preset.")
    resolved_mode: str = Field(description="Resolved workspace mode after preset expansion.")
    state: str = Field(description="Workspace preflight state.")
    input_cwd: str = Field(description="Input cwd before workspace resolution.")
    repo_root: str | None = Field(default=None, description="Detected git repo root, if any.")
    effective_cwd: str = Field(description="Exact cwd Dispatch will pass to App Server.")
    environment_file: str | None = Field(
        default=None, description="Resolved .codex environment file, if discovered."
    )
    environment: WorkspaceEnvironmentView | None = None
    setup: WorkspaceSetupView = Field(
        default_factory=lambda: WorkspaceSetupView(policy="none", ran=False)
    )
    worktree: WorktreeView = Field(default_factory=lambda: WorktreeView(state="disabled"))


class LaunchPlan(BaseModel):
    """A mutation-free preview of what ``dispatch new`` would launch (``--dry-run``)."""

    name: str = Field(description="Resolved display name (after prefix/presets).")
    handle: str = Field(description="Resolved @handle the lane would receive.")
    cwd: str = Field(description="Resolved working directory.")
    workspace: WorkspaceView = Field(description="Workspace preflight plan.")
    packet: str | None = Field(default=None, description="Resolved packet directory, if any.")
    settings: LaunchSettingsView = Field(description="Effective lane settings.")
    sources: list[LaunchInputSource] = Field(
        default_factory=list, description="Resolved launch input sources."
    )
    goal_set: bool = Field(description="Whether a native goal would be created.")
    would_send: bool = Field(description="Whether an initial turn would start.")
    output_schema_present: bool = Field(
        description="Whether a structured output schema would be applied."
    )
    stage: StageView = Field(
        default_factory=StageView, description="Packet parts that would be staged to disk."
    )
    unknown_packet_files: list[str] = Field(
        default_factory=list, description="Packet files with no defined launch meaning."
    )
    aux_packet_dirs: list[str] = Field(
        default_factory=list,
        description="Packet directories staged-but-not-executed by dispatch (hooks/, codex/).",
    )


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


class HistoryWorktree(BaseModel):
    detected: bool = False
    path: str | None = None
    repo: str | None = None
    branch: str | None = None
    head: str | None = None
    dirty: bool = False
    changed_files_count: int = 0
    changed_files: list[str] = Field(default_factory=list)
    is_codex_worktree: bool = False


class HistoryToolStat(BaseModel):
    tool: str
    count: int
    item_types: list[str] = Field(default_factory=list)


class HistoryFileStat(BaseModel):
    path: str
    count: int


class HistoryItem(TranscriptItem):
    role: str | None = None
    tool: str | None = None
    files: list[str] = Field(default_factory=list)
    raw: dict[str, object] | None = None


class HistoryThreadSummary(BaseModel):
    ref: str | None = None
    id: str
    handle: str | None = None
    source: LaneSource | None = None
    status: LaneStatus | None = None
    cwd: str | None = None
    first_event_at: str | None = None
    last_event_at: str | None = None
    turns: int = 0
    items: int = 0
    messages: int = 0
    tool_calls: int = 0
    unique_tools: list[str] = Field(default_factory=list)
    files_changed_count: int = 0
    files_changed: list[HistoryFileStat] = Field(default_factory=list)
    transcript_bytes: int | None = None
    estimated_tokens: int | None = None
    subagents_count: int = 0
    subagent_thread_ids: list[str] = Field(default_factory=list)
    worktree: HistoryWorktree = Field(default_factory=HistoryWorktree)


class HistoryOutput(BaseModel):
    mode: Literal["overview", "summary", "items", "tools", "files"]
    threads: list[HistoryThreadSummary] = Field(default_factory=list)
    thread: HistoryThreadSummary | None = None
    items: list[HistoryItem] = Field(default_factory=list)
    tools: list[HistoryToolStat] = Field(default_factory=list)
    files: list[HistoryFileStat] = Field(default_factory=list)


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
    catalog_state: Literal["ready", "empty"] = "ready"
    hint: str | None = None
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
