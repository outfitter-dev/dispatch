"""The ``Op`` — one authored operation, the single source every surface derives
from (ADR-0000).

Author only what's irreducible: input/output Pydantic models, ``intent``,
``idempotent``, examples, and an async handler. ``define_op`` is the one
type-erasure boundary: it type-checks that the handler matches its input/output
models, then stores the op uniformly so the registry and derivations stay
non-generic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel

from .context import Ctx
from .errors import DispatchError

Intent = Literal["read", "write", "destroy"]


@dataclass(frozen=True)
class Example:
    """An op example = a test + a doc. Asserts either ``output`` or ``raises``."""

    name: str
    input: dict[str, object]
    output: dict[str, object] | None = None
    raises: type[DispatchError] | None = None


Handler = Callable[[BaseModel, Ctx], Awaitable[BaseModel]]


@dataclass(frozen=True)
class Op:
    """One operation. Authored once; projected to every surface."""

    id: str
    summary: str
    input: type[BaseModel]
    output: type[BaseModel]
    intent: Intent
    idempotent: bool
    handler: Handler
    examples: tuple[Example, ...] = ()


def define_op[I: BaseModel, O: BaseModel](
    *,
    id: str,
    summary: str,
    input: type[I],
    output: type[O],
    intent: Intent,
    idempotent: bool,
    handler: Callable[[I, Ctx], Awaitable[O]],
    examples: Sequence[Example] = (),
) -> Op:
    """Author an op. The handler is type-checked against ``input``/``output``
    here; the single ``cast`` is the deliberate erasure boundary so the registry
    can hold heterogeneous ops without ``Any`` leaking into surfaces."""
    return Op(
        id=id,
        summary=summary,
        input=input,
        output=output,
        intent=intent,
        idempotent=idempotent,
        handler=cast("Handler", handler),
        examples=tuple(examples),
    )
