"""Op execution: validate input at the boundary, run the handler, dump output.

The one place external params become a typed model. Pydantic validation failures
become a ``ValidationError`` (part of the taxonomy) so surfaces project them
uniformly. Surfaces call this; handlers stay surface-agnostic.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError as PydanticValidationError

from .context import Ctx
from .errors import ValidationError
from .op import Op


async def execute(op: Op, params: Mapping[str, object], ctx: Ctx) -> dict[str, object]:
    try:
        parsed = op.input.model_validate(dict(params))
    except PydanticValidationError as exc:
        raise ValidationError(
            f"invalid input for {op.id!r}: {exc.errors(include_url=False)}"
        ) from exc
    output = await op.handler(parsed, ctx)
    return output.model_dump(mode="python")
