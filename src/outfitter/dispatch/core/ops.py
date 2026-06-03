"""The v1 op registry: each op authored once (input/output/intent/idempotent/
examples/handler), registered here. Surfaces derive from ``REGISTRY``; adding a
capability is adding an op here and nothing else.
"""

from __future__ import annotations

from outfitter.dispatch.contracts.errors import NotFoundError
from outfitter.dispatch.contracts.op import Example, define_op
from outfitter.dispatch.contracts.registry import OpRegistry

from . import handlers
from .models import (
    ActionAck,
    AttachInput,
    LaneDetail,
    LaneInput,
    LaneRef,
    LaneTextInput,
    OpenInput,
    Roster,
    RosterInput,
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

_ALL = (OPEN, ATTACH, SEND, STEER, BRIEF, INTERRUPT, SHOW, ROSTER, ARCHIVE)


def build_registry() -> OpRegistry:
    registry = OpRegistry()
    for op in _ALL:
        registry.register(op)
    return registry


REGISTRY = build_registry()
