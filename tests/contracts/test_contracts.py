"""Unit tests for the contract machinery: define_op, OpRegistry, run_examples."""

from __future__ import annotations

import asyncio

import pytest
import structlog
from pydantic import BaseModel

from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import NotFoundError
from outfitter.dispatch.contracts.examples import run_examples
from outfitter.dispatch.contracts.op import Example, define_op
from outfitter.dispatch.contracts.registry import OpRegistry
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient


class EchoIn(BaseModel):
    text: str


class EchoOut(BaseModel):
    echo: str


async def echo_handler(inp: EchoIn, ctx: Ctx) -> EchoOut:
    return EchoOut(echo=inp.text)


class LookupIn(BaseModel):
    lane: str


class LookupOut(BaseModel):
    handle: str


async def lookup_handler(inp: LookupIn, ctx: Ctx) -> LookupOut:
    lane = await ctx.registry.get_lane(inp.lane)  # raises NotFoundError if absent
    return LookupOut(handle=lane.handle)


ECHO = define_op(
    id="echo",
    summary="Echo the input text.",
    input=EchoIn,
    output=EchoOut,
    intent="read",
    idempotent=True,
    handler=echo_handler,
    examples=[Example("basic", input={"text": "hi"}, output={"echo": "hi"})],
)

LOOKUP = define_op(
    id="lookup",
    summary="Look up a lane handle.",
    input=LookupIn,
    output=LookupOut,
    intent="read",
    idempotent=True,
    handler=lookup_handler,
    examples=[Example("missing", input={"lane": "nope"}, raises=NotFoundError)],
)


def test_op_registry_register_get_and_duplicate() -> None:
    registry = OpRegistry()
    registry.register(ECHO)
    registry.register(LOOKUP)
    assert registry.ids() == ["echo", "lookup"]
    assert registry.get("echo").summary == "Echo the input text."
    with pytest.raises(ValueError, match="duplicate op id"):
        registry.register(ECHO)


def test_define_op_preserves_intent_and_idempotent() -> None:
    assert ECHO.intent == "read"
    assert ECHO.idempotent is True
    assert ECHO.input is EchoIn


async def test_run_examples_checks_output_and_raises() -> None:
    store = await Registry.open()

    async def make_ctx() -> Ctx:
        return Ctx(
            client=FakeLaneClient(),
            registry=store,
            log=structlog.get_logger(),
            abort=asyncio.Event(),
        )

    try:
        ran = await run_examples([ECHO, LOOKUP], make_ctx)
        assert ran == 2  # one output example + one raises example, both passed
    finally:
        await store.close()


async def test_run_examples_surfaces_output_mismatch() -> None:
    store = await Registry.open()
    bad = define_op(
        id="bad",
        summary="Wrong expected output.",
        input=EchoIn,
        output=EchoOut,
        intent="read",
        idempotent=True,
        handler=echo_handler,
        examples=[Example("wrong", input={"text": "hi"}, output={"echo": "WRONG"})],
    )

    async def make_ctx() -> Ctx:
        return Ctx(
            client=FakeLaneClient(),
            registry=store,
            log=structlog.get_logger(),
            abort=asyncio.Event(),
        )

    try:
        with pytest.raises(AssertionError, match="output"):
            await run_examples([bad], make_ctx)
    finally:
        await store.close()
