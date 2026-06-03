"""The contract layer: ops authored once, surfaces derived (ADR-0000).

Author what's new (an ``Op`` in this package), derive what's known (CLI/MCP/remote
projections), override what's wrong. This module re-exports the authoring API.
"""

from __future__ import annotations

from .context import Ctx, LaneClient
from .errors import (
    ApprovalRequiredError,
    AppServerError,
    AuthorityError,
    DispatchError,
    ErrorProjection,
    LaneBusyError,
    NotFoundError,
    ValidationError,
    project_error,
)
from .examples import run_examples
from .op import Example, Intent, Op, define_op
from .registry import OpRegistry

__all__ = [
    "AppServerError",
    "ApprovalRequiredError",
    "AuthorityError",
    "Ctx",
    "DispatchError",
    "ErrorProjection",
    "Example",
    "Intent",
    "LaneBusyError",
    "LaneClient",
    "NotFoundError",
    "Op",
    "OpRegistry",
    "ValidationError",
    "define_op",
    "project_error",
    "run_examples",
]
