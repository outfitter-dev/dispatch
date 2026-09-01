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
from outfitter.dispatch.contracts.registry import OpRegistry, registry_legacy_safe_ops
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


def test_registry_legacy_safe_ops_excludes_reads_sharing_write_input() -> None:
    """A read op whose input model a non-read op shares (a dry-run preview of a
    write) is NOT safe to send to a daemon that predates the schema handshake —
    it carries the same skew-sensitive fields the write is gated on."""

    async def preview_handler(inp: EchoIn, ctx: Ctx) -> EchoOut:
        return EchoOut(echo=inp.text)

    write = define_op(
        id="write",
        summary="Mutate using EchoIn.",
        input=EchoIn,
        output=EchoOut,
        intent="write",
        idempotent=False,
        handler=echo_handler,
        examples=[Example("basic", input={"text": "hi"}, output={"echo": "hi"})],
    )
    preview = define_op(
        id="write-plan",
        summary="Preview the write without mutating.",
        input=EchoIn,
        output=EchoOut,
        intent="read",
        idempotent=True,
        handler=preview_handler,
        examples=[Example("basic", input={"text": "hi"}, output={"echo": "hi"})],
    )
    registry = OpRegistry()
    registry.register(write)
    registry.register(preview)
    registry.register(LOOKUP)  # read, input model not shared with any write

    assert registry_legacy_safe_ops(registry) == frozenset({"lookup"})


def test_registry_legacy_safe_ops_real_registry_gates_new_plan() -> None:
    """``new`` and ``new-plan`` carry the drifted ``NewInput`` (``provider``),
    so neither is exempt on a pre-handshake daemon; plain reads like ``roster``
    stay safe, and baseline-matching writes like ``stop`` stay usable so a
    pre-handshake daemon with active work can still be drained."""
    from outfitter.dispatch.core.ops import REGISTRY

    safe = registry_legacy_safe_ops(REGISTRY)
    assert "roster" in safe
    assert "status" in safe
    assert "stop" in safe  # write-intent, but schema unchanged since the parent release
    assert "new" not in safe
    assert "new-plan" not in safe


def test_prehandshake_op_allowed_gates_baseline_ops_by_reported_version() -> None:
    """Read-safe ops require at least ``READ_BASELINE_FLOOR`` (read inputs
    evolved too — v0.8.1's ``roster`` had no ``parent``); baseline-matching
    ops require the daemon to self-report exactly the parent release — the
    baseline proves parity with that release only (e.g. v0.8.2's ``send`` had
    no ``content`` field). Deliberate policy change: reads used to pass at
    any (or no) version; sdist evidence showed read schemas drift too, so a
    no-metadata daemon (<= 0.8.1) now gets nothing."""
    from outfitter.dispatch.contracts.legacy_baseline import (
        PARENT_VERSION,
        READ_BASELINE_FLOOR,
    )
    from outfitter.dispatch.contracts.registry import prehandshake_op_allowed

    # Read-safe: at or above the evidence-backed floor.
    assert prehandshake_op_allowed(READ_BASELINE_FLOOR, read_safe=True, baseline_safe=False)
    assert prehandshake_op_allowed(PARENT_VERSION, read_safe=True, baseline_safe=True)
    # Below the floor, unreported, or unparseable: blocked, even for reads.
    assert not prehandshake_op_allowed("0.9.0", read_safe=True, baseline_safe=False)
    assert not prehandshake_op_allowed("0.2.0", read_safe=True, baseline_safe=False)
    assert not prehandshake_op_allowed(None, read_safe=True, baseline_safe=True)
    assert not prehandshake_op_allowed(0.11, read_safe=True, baseline_safe=False)
    assert not prehandshake_op_allowed("0.11.0.dev1", read_safe=True, baseline_safe=False)

    # Baseline-only: exact parent version required.
    assert prehandshake_op_allowed(PARENT_VERSION, read_safe=False, baseline_safe=True)
    assert not prehandshake_op_allowed("0.10.0", read_safe=False, baseline_safe=True)
    assert not prehandshake_op_allowed(None, read_safe=False, baseline_safe=True)
    assert not prehandshake_op_allowed(0.11, read_safe=False, baseline_safe=True)

    # Neither: never forwarded pre-handshake.
    assert not prehandshake_op_allowed(PARENT_VERSION, read_safe=False, baseline_safe=False)


def test_registry_read_safe_ops_is_subset_without_baseline_writes() -> None:
    """The read-safe set (floor-gated at the pre-flight) is a subset of the
    legacy-safe set: reads like ``roster`` are in, baseline writes like
    ``stop`` are not."""
    from outfitter.dispatch.contracts.registry import registry_read_safe_ops
    from outfitter.dispatch.core.ops import REGISTRY

    read_safe = registry_read_safe_ops(REGISTRY)
    assert read_safe <= registry_legacy_safe_ops(REGISTRY)
    assert "roster" in read_safe
    assert "stop" not in read_safe
    assert "new-plan" not in read_safe


def test_legacy_baseline_partitions_registry_and_matches_current_hashes() -> None:
    """Staleness guard: every op is either baseline-matching or consciously
    listed in ``CHANGED_SINCE_PARENT``. Fails loudly on silent schema drift —
    the fix is to add the op id to CHANGED_SINCE_PARENT (declaring it unsafe
    against the parent-release daemon), or at release cut to regenerate the
    baseline with ``scripts/gen_legacy_baseline.py``."""
    from outfitter.dispatch.contracts.legacy_baseline import (
        CHANGED_SINCE_PARENT,
        PARENT_OP_SCHEMA_HASHES,
        PARENT_VERSION,
    )
    from outfitter.dispatch.contracts.registry import op_schema_hash
    from outfitter.dispatch.core.ops import REGISTRY

    assert PARENT_VERSION
    baseline_ids = set(PARENT_OP_SCHEMA_HASHES)
    registry_ids = set(REGISTRY.ids())
    overlap = baseline_ids & CHANGED_SINCE_PARENT
    assert not overlap, f"ops both baselined and marked changed: {sorted(overlap)}"
    assert baseline_ids | CHANGED_SINCE_PARENT == registry_ids, (
        "legacy_baseline.py does not partition the registry: "
        f"missing={sorted(registry_ids - baseline_ids - CHANGED_SINCE_PARENT)} "
        f"stale={sorted((baseline_ids | CHANGED_SINCE_PARENT) - registry_ids)}"
    )
    drifted = sorted(
        op.id
        for op in REGISTRY
        if op.id in PARENT_OP_SCHEMA_HASHES and op_schema_hash(op) != PARENT_OP_SCHEMA_HASHES[op.id]
    )
    assert not drifted, (
        f"op schemas drifted from the v{PARENT_VERSION} baseline: {drifted}. "
        "Add each to CHANGED_SINCE_PARENT in contracts/legacy_baseline.py (and drop "
        "its hash) so pre-handshake daemons stop receiving it."
    )


def test_read_baseline_floor_is_sound_against_the_parent_baseline() -> None:
    """The read allowance floor is only sound while every read-safe op's
    schema is baseline-matching: the sdist evidence behind
    ``READ_BASELINE_FLOOR`` covers floor..parent, and the parent baseline
    extends it to the current tree. If a read-safe op's schema drifts (it
    lands in ``CHANGED_SINCE_PARENT``), raise the floor past the parent
    release (to the first release carrying the new schema) so no shipped
    pre-handshake daemon silently drops the new field."""
    from outfitter.dispatch.contracts.legacy_baseline import (
        CHANGED_SINCE_PARENT,
        PARENT_VERSION,
        READ_BASELINE_FLOOR,
    )
    from outfitter.dispatch.contracts.registry import (
        _version_tuple,
        registry_read_safe_ops,
    )
    from outfitter.dispatch.core.ops import REGISTRY

    floor = _version_tuple(READ_BASELINE_FLOOR)
    parent = _version_tuple(PARENT_VERSION)
    assert floor is not None, "READ_BASELINE_FLOOR must be a plain X.Y.Z version"
    assert parent is not None

    drifted_reads = sorted(registry_read_safe_ops(REGISTRY) & CHANGED_SINCE_PARENT)
    if floor <= parent:
        assert not drifted_reads, (
            f"read-safe ops drifted since v{PARENT_VERSION}: {drifted_reads}. "
            "A pre-handshake daemon at or above READ_BASELINE_FLOOR would silently "
            "drop the changed fields — raise READ_BASELINE_FLOOR in "
            "contracts/legacy_baseline.py past the parent release."
        )


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
