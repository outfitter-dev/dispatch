"""The op registry — the durable collection every surface projects from.

Authoring a capability = adding an op + registering it here. Nothing else.
"""

from __future__ import annotations

from collections.abc import Iterator

from .op import Op


class OpRegistry:
    """An ordered, name-unique collection of ops."""

    def __init__(self) -> None:
        self._ops: dict[str, Op] = {}

    def register(self, op: Op) -> Op:
        if op.id in self._ops:
            raise ValueError(f"duplicate op id: {op.id!r}")
        self._ops[op.id] = op
        return op

    def get(self, op_id: str) -> Op:
        try:
            return self._ops[op_id]
        except KeyError:
            raise KeyError(f"unknown op id: {op_id!r}") from None

    def ids(self) -> list[str]:
        return list(self._ops)

    def __iter__(self) -> Iterator[Op]:
        return iter(self._ops.values())

    def __len__(self) -> int:
        return len(self._ops)
