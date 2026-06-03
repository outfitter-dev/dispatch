"""Examples-as-tests: run every op's examples as assertions.

``run_examples`` validates each example's input into the op's model, runs the
handler against a freshly-built ``Ctx`` (fakes injected by the caller), and
asserts the declared ``output`` or ``raises``. The pytest wrapper
(``test_examples``) just supplies a ``Ctx`` factory and calls this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from .context import Ctx
from .errors import DispatchError
from .op import Example, Op

CtxFactory = Callable[[], Awaitable[Ctx]]


async def run_examples(ops: Iterable[Op], make_ctx: CtxFactory) -> int:
    """Run all examples; raise ``AssertionError`` on the first mismatch. Returns
    the number of examples run."""
    count = 0
    for op in ops:
        for example in op.examples:
            await _run_one(op, example, make_ctx)
            count += 1
    return count


async def _run_one(op: Op, example: Example, make_ctx: CtxFactory) -> None:
    where = f"{op.id}/{example.name}"
    parsed = op.input.model_validate(example.input)
    ctx = await make_ctx()
    if example.raises is not None:
        try:
            await op.handler(parsed, ctx)
        except DispatchError as exc:
            if not isinstance(exc, example.raises):
                raise AssertionError(
                    f"{where}: expected {example.raises.__name__}, got {type(exc).__name__}: {exc}"
                ) from exc
            return
        raise AssertionError(
            f"{where}: expected {example.raises.__name__}, but no error was raised"
        )
    output = await op.handler(parsed, ctx)
    if example.output is not None:
        got = output.model_dump(mode="python")
        if got != example.output:
            raise AssertionError(f"{where}: output {got!r} != expected {example.output!r}")
