"""Examples-as-tests over the real op registry (the contracts.md gate)."""

from __future__ import annotations

from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.examples import run_examples
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.registry.store import Registry
from tests.fakes import make_ctx


async def test_registry_has_the_v1_ops() -> None:
    assert REGISTRY.ids() == [
        "open",
        "new",
        "attach",
        "send",
        "stop",
        "show",
        "lane-rename",
        "transcript",
        "watch",
        "sync",
        "roster",
        "discover",
        "search",
        "archive",
        "restore",
        "goal-get",
        "goal-set",
        "goal-clear",
        "fork",
        "rollback",
        "compact",
        "status",
        "log",
        "trigger-add",
        "trigger-list",
        "trigger-rm",
        "trigger-pause",
        "trigger-resume",
    ]


async def test_examples() -> None:
    stores: list[Registry] = []

    async def factory() -> Ctx:
        store = await Registry.open()  # fresh, isolated store per example
        stores.append(store)
        return make_ctx(store)

    for op in REGISTRY:
        assert len(op.examples) >= 1, f"op {op.id!r} has no examples"
    try:
        ran = await run_examples(REGISTRY, factory)
        assert ran >= len(list(REGISTRY))  # every op contributed at least one example
    finally:
        for store in stores:
            await store.close()
