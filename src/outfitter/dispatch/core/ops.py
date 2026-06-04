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
    DiscoverInput,
    Discovery,
    LaneDetail,
    LaneInput,
    LaneRef,
    LaneTextInput,
    LogInput,
    LogOutput,
    NewInput,
    NewLane,
    OpenInput,
    Roster,
    RosterInput,
    StatusInput,
    StatusOutput,
    TriggerAddInput,
    TriggerIdInput,
    TriggerList,
    TriggerListInput,
    TriggerRemoved,
    TriggerView,
)

OPEN = define_op(
    id="open",
    summary="Open a new owned lane.",
    input=OpenInput,
    output=LaneRef,
    intent="write",
    idempotent=False,
    handler=handlers.open_lane,
    examples=[
        Example(
            "alpha",
            input={"name": "alpha", "cwd": "."},
            output={"id": "lane-1", "handle": "@alpha", "source": "own", "status": "idle"},
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
            input={"name": "alpha", "send": False},
            output={
                "id": "lane-1",
                "handle": "@[dispatch] alpha",
                "source": "own",
                "status": "idle",
                "sent": False,
            },
        )
    ],
)

ATTACH = define_op(
    id="attach",
    summary="Attach to an existing lane (observe-only; ADR-0005).",
    input=AttachInput,
    output=LaneRef,
    intent="write",
    idempotent=True,
    handler=handlers.attach_lane,
    examples=[
        Example(
            "resume",
            input={"thread": "T1"},
            output={"id": "T1", "handle": "@T1", "source": "attached", "status": "idle"},
        )
    ],
)

SEND = define_op(
    id="send",
    summary="Send a message to a lane (starts a turn).",
    input=LaneTextInput,
    output=ActionAck,
    intent="write",
    idempotent=False,
    handler=handlers.send,
    examples=[Example("missing", input={"lane": "nope", "text": "hi"}, raises=NotFoundError)],
)

STEER = define_op(
    id="steer",
    summary="Interject into a lane's active turn.",
    input=LaneTextInput,
    output=ActionAck,
    intent="write",
    idempotent=False,
    handler=handlers.steer,
    examples=[Example("missing", input={"lane": "nope", "text": "x"}, raises=NotFoundError)],
)

BRIEF = define_op(
    id="brief",
    summary="Silently inject context into a lane (no turn runs).",
    input=LaneTextInput,
    output=ActionAck,
    intent="write",
    idempotent=False,
    handler=handlers.brief,
    examples=[Example("missing", input={"lane": "nope", "text": "fyi"}, raises=NotFoundError)],
)

INTERRUPT = define_op(
    id="interrupt",
    summary="Cancel a lane's active turn.",
    input=LaneInput,
    output=ActionAck,
    intent="write",
    idempotent=True,
    handler=handlers.interrupt,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

SHOW = define_op(
    id="show",
    summary="Show a lane's current detail.",
    input=LaneInput,
    output=LaneDetail,
    intent="read",
    idempotent=True,
    handler=handlers.show,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)

ROSTER = define_op(
    id="roster",
    summary="List managed lanes.",
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

ARCHIVE = define_op(
    id="archive",
    summary="Archive a lane (reversible).",
    input=LaneInput,
    output=LaneRef,
    intent="destroy",
    idempotent=True,
    handler=handlers.archive,
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
    STEER,
    BRIEF,
    INTERRUPT,
    SHOW,
    ROSTER,
    DISCOVER,
    ARCHIVE,
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
