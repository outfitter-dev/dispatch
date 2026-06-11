"""The v1 op registry: each op authored once (input/output/intent/idempotent/
examples/handler), registered here. Surfaces derive from ``REGISTRY``; adding a
capability is adding an op here and nothing else.
"""

from __future__ import annotations

from outfitter.dispatch.contracts.errors import NotFoundError, ValidationError
from outfitter.dispatch.contracts.op import Example, define_op
from outfitter.dispatch.contracts.registry import OpRegistry

from . import handlers, trigger_handlers
from .models import (
    ActionAck,
    AttachInput,
    CompactInput,
    DiscoverInput,
    Discovery,
    ForkInput,
    GoalClearInput,
    GoalGetInput,
    GoalSetInput,
    GoalView,
    LaneDetail,
    LaneInput,
    LaneRef,
    LaneRenameInput,
    LaneSyncInput,
    LaneSyncResult,
    LogInput,
    LogOutput,
    ModelCatalogOutput,
    ModelsInput,
    NewInput,
    NewLane,
    OpenInput,
    RollbackInput,
    Roster,
    RosterInput,
    SearchInput,
    SearchOutput,
    SendInput,
    ShowInput,
    StatusInput,
    StatusOutput,
    ThreadActionRef,
    ThreadTargetInput,
    TranscriptInput,
    TranscriptOutput,
    TriggerAddInput,
    TriggerIdInput,
    TriggerList,
    TriggerListInput,
    TriggerRemoved,
    TriggerView,
    WatchInput,
    WatchOutput,
)

OPEN = define_op(
    id="open",
    summary="Primitive: start and register a new owned managed thread.",
    input=OpenInput,
    output=LaneRef,
    intent="write",
    idempotent=False,
    handler=handlers.open_lane,
    examples=[
        Example(
            "alpha",
            input={"name": "alpha", "cwd": "."},
            output={
                "ref": "0BGeK1",
                "id": "lane-1",
                "handle": "@alpha",
                "source": "own",
                "status": "idle",
                "cwd": ".",
            },
        )
    ],
)

NEW = define_op(
    id="new",
    summary="Create a configured owned lane and optionally send an initial message.",
    input=NewInput,
    output=NewLane,
    intent="write",
    idempotent=False,
    handler=handlers.new_lane,
    examples=[
        Example(
            "idle",
            input={"name": "alpha", "cwd": "/work", "prefix": "[dispatch]", "send": False},
            output={
                "ref": "0BGeK1",
                "id": "lane-1",
                "handle": "@[dispatch] alpha",
                "source": "own",
                "status": "idle",
                "cwd": "/work",
                "message_accepted": False,
                "goal_set": False,
                "latest_turn": {
                    "id": None,
                    "status": None,
                    "error": None,
                    "error_at": None,
                },
                "model": {
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "reasoning_effort": "xhigh",
                    "service_tier": {
                        "requested": None,
                        "resolved": None,
                        "name": None,
                        "source": "unknown",
                    },
                },
            },
        )
    ],
)

ATTACH = define_op(
    id="attach",
    summary="Attach to an existing Codex thread (turn-write locked; ADR-0005).",
    input=AttachInput,
    output=LaneRef,
    intent="write",
    idempotent=True,
    handler=handlers.attach_lane,
    examples=[
        Example(
            "resume",
            input={"thread": "T1"},
            output={
                "ref": "0ABFs1",
                "id": "T1",
                "handle": "@T1",
                "source": "attached",
                "status": "idle",
                "cwd": None,
            },
        )
    ],
)

SEND = define_op(
    id="send",
    summary="Send, steer, interject, queue, or inject context into a managed thread.",
    input=SendInput,
    output=ActionAck,
    intent="write",
    idempotent=False,
    handler=handlers.send_message,
    examples=[
        Example("missing", input={"lane": "nope", "text": "hi"}, raises=NotFoundError),
    ],
)

STOP = define_op(
    id="stop",
    summary="Cancel a managed thread's active turn.",
    input=LaneInput,
    output=ActionAck,
    intent="write",
    idempotent=True,
    handler=handlers.stop,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

SHOW = define_op(
    id="show",
    summary="Show a managed thread's current detail.",
    input=ShowInput,
    output=LaneDetail,
    intent="read",
    idempotent=True,
    handler=handlers.show,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

LANE_RENAME = define_op(
    id="lane-rename",
    summary="Rename a managed thread label or unmanaged Codex thread.",
    input=LaneRenameInput,
    output=ThreadActionRef,
    intent="write",
    idempotent=False,
    handler=handlers.rename_lane,
    examples=[
        Example(
            "unmanaged",
            input={"old": "T1", "new": "docs"},
            output={
                "ref": None,
                "id": "T1",
                "handle": None,
                "managed": False,
                "source": "unmanaged",
                "status": None,
                "cwd": None,
            },
        )
    ],
)

TRANSCRIPT = define_op(
    id="transcript",
    summary="Read compact persisted history for a managed thread.",
    input=TranscriptInput,
    output=TranscriptOutput,
    intent="read",
    idempotent=True,
    handler=handlers.transcript,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

WATCH = define_op(
    id="watch",
    summary="Collect a bounded live App Server event sample for a managed thread.",
    input=WatchInput,
    output=WatchOutput,
    intent="read",
    idempotent=True,
    handler=handlers.watch,
    examples=[Example("missing", input={"lane": "nope", "timeout": 0}, raises=NotFoundError)],
)

SYNC = define_op(
    id="sync",
    summary="Refresh a managed thread's local dispatch index.",
    input=LaneSyncInput,
    output=LaneSyncResult,
    intent="write",
    idempotent=True,
    handler=handlers.sync_lane,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

ROSTER = define_op(
    id="roster",
    summary="List managed threads.",
    input=RosterInput,
    output=Roster,
    intent="read",
    idempotent=True,
    handler=handlers.roster,
    examples=[Example("empty", input={}, output={"lanes": []})],
)

DISCOVER = define_op(
    id="discover",
    summary="List persisted Codex sessions you could attach (distinct from roster).",
    input=DiscoverInput,
    output=Discovery,
    intent="read",
    idempotent=True,
    handler=handlers.discover,
    examples=[Example("empty", input={"limit": 50}, output={"sessions": []})],
)

SEARCH = define_op(
    id="search",
    summary="Search Codex thread history.",
    input=SearchInput,
    output=SearchOutput,
    intent="read",
    idempotent=True,
    handler=handlers.search,
    examples=[
        Example(
            "empty",
            input={"query": "needle"},
            output={
                "query": "needle",
                "matches": [],
                "scanned": 0,
                "next_cursor": None,
                "experimental": True,
            },
        )
    ],
)

MODELS = define_op(
    id="models",
    summary="List available Codex models and service tiers from the App Server catalog.",
    input=ModelsInput,
    output=ModelCatalogOutput,
    intent="read",
    idempotent=True,
    handler=handlers.models,
    examples=[
        Example(
            "empty",
            input={"refresh": False},
            output={
                "refreshed_at": None,
                "source": "registry",
                "configured_default": {
                    "model": "gpt-5.5",
                    "model_provider": "openai",
                    "service_tier": None,
                    "model_reasoning_effort": "xhigh",
                },
                "models": [],
            },
        )
    ],
)

ARCHIVE = define_op(
    id="archive",
    summary="Archive a managed or unmanaged Codex thread.",
    input=ThreadTargetInput,
    output=ThreadActionRef,
    intent="destroy",
    idempotent=True,
    handler=handlers.archive,
    examples=[
        Example(
            "unmanaged",
            input={"target": "T1"},
            output={
                "ref": None,
                "id": "T1",
                "handle": None,
                "managed": False,
                "source": "unmanaged",
                "status": "archived",
                "cwd": None,
            },
        )
    ],
)

RESTORE = define_op(
    id="restore",
    summary="Restore an archived managed or unmanaged Codex thread.",
    input=ThreadTargetInput,
    output=ThreadActionRef,
    intent="write",
    idempotent=True,
    handler=handlers.restore,
    examples=[
        Example(
            "unmanaged",
            input={"target": "T1"},
            output={
                "ref": None,
                "id": "T1",
                "handle": None,
                "managed": False,
                "source": "unmanaged",
                "status": "unknown",
                "cwd": None,
            },
        )
    ],
)

GOAL_GET = define_op(
    id="goal-get",
    summary="Read a managed thread's native App Server goal.",
    input=GoalGetInput,
    output=GoalView,
    intent="read",
    idempotent=True,
    handler=handlers.goal_get,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

GOAL_SET = define_op(
    id="goal-set",
    summary="Set or update a managed thread's native App Server goal.",
    input=GoalSetInput,
    output=GoalView,
    intent="write",
    idempotent=False,
    handler=handlers.goal_set,
    examples=[
        Example("missing", input={"lane": "nope", "objective": "ship"}, raises=NotFoundError)
    ],
)

GOAL_CLEAR = define_op(
    id="goal-clear",
    summary="Clear a managed thread's native App Server goal.",
    input=GoalClearInput,
    output=GoalView,
    intent="destroy",
    idempotent=True,
    handler=handlers.goal_clear,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

FORK = define_op(
    id="fork",
    summary="Fork a managed thread into a new owned managed thread.",
    input=ForkInput,
    output=LaneRef,
    intent="write",
    idempotent=False,
    handler=handlers.fork,
    examples=[Example("missing", input={"lane": "nope", "name": "copy"}, raises=NotFoundError)],
)

ROLLBACK = define_op(
    id="rollback",
    summary="Rollback persisted managed-thread history; does not revert workspace files.",
    input=RollbackInput,
    output=LaneRef,
    intent="destroy",
    idempotent=False,
    handler=handlers.rollback,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

COMPACT = define_op(
    id="compact",
    summary="Start App Server context compaction for a managed thread.",
    input=CompactInput,
    output=ActionAck,
    intent="write",
    idempotent=False,
    handler=handlers.compact,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

TRIGGER_ADD = define_op(
    id="trigger-add",
    summary="Add a trigger (when -> action -> lane).",
    input=TriggerAddInput,
    output=TriggerView,
    intent="write",
    idempotent=False,
    handler=trigger_handlers.trigger_add,
    examples=[
        Example(
            "interval-needs-seconds",
            input={"name": "p", "lane": "@x", "when": "interval", "action": "send", "text": "hi"},
            raises=ValidationError,
        )
    ],
)

TRIGGER_LIST = define_op(
    id="trigger-list",
    summary="List triggers.",
    input=TriggerListInput,
    output=TriggerList,
    intent="read",
    idempotent=True,
    handler=trigger_handlers.trigger_list,
    examples=[Example("empty", input={}, output={"triggers": []})],
)

TRIGGER_RM = define_op(
    id="trigger-rm",
    summary="Remove a trigger.",
    input=TriggerIdInput,
    output=TriggerRemoved,
    intent="destroy",
    idempotent=True,
    handler=trigger_handlers.trigger_rm,
    examples=[Example("missing", input={"id": "nope"}, raises=NotFoundError)],
)

TRIGGER_PAUSE = define_op(
    id="trigger-pause",
    summary="Pause a trigger.",
    input=TriggerIdInput,
    output=TriggerView,
    intent="write",
    idempotent=True,
    handler=trigger_handlers.trigger_pause,
    examples=[Example("missing", input={"id": "nope"}, raises=NotFoundError)],
)

TRIGGER_RESUME = define_op(
    id="trigger-resume",
    summary="Resume a paused trigger.",
    input=TriggerIdInput,
    output=TriggerView,
    intent="write",
    idempotent=True,
    handler=trigger_handlers.trigger_resume,
    examples=[Example("missing", input={"id": "nope"}, raises=NotFoundError)],
)

STATUS = define_op(
    id="status",
    summary="Show daemon health: lane and trigger counts.",
    input=StatusInput,
    output=StatusOutput,
    intent="read",
    idempotent=True,
    handler=handlers.status,
    examples=[
        Example(
            "empty",
            input={},
            output={"lanes": 0, "idle": 0, "busy": 0, "triggers": 0, "triggers_enabled": 0},
        )
    ],
)

LOG = define_op(
    id="log",
    summary="Show the recent actions audit log.",
    input=LogInput,
    output=LogOutput,
    intent="read",
    idempotent=True,
    handler=handlers.show_log,
    examples=[Example("empty", input={"limit": 5}, output={"actions": []})],
)

_ALL = (
    OPEN,
    NEW,
    ATTACH,
    SEND,
    STOP,
    SHOW,
    LANE_RENAME,
    TRANSCRIPT,
    WATCH,
    SYNC,
    ROSTER,
    DISCOVER,
    SEARCH,
    MODELS,
    ARCHIVE,
    RESTORE,
    GOAL_GET,
    GOAL_SET,
    GOAL_CLEAR,
    FORK,
    ROLLBACK,
    COMPACT,
    STATUS,
    LOG,
    TRIGGER_ADD,
    TRIGGER_LIST,
    TRIGGER_RM,
    TRIGGER_PAUSE,
    TRIGGER_RESUME,
)


def build_registry() -> OpRegistry:
    registry = OpRegistry()
    for op in _ALL:
        registry.register(op)
    return registry


REGISTRY = build_registry()
