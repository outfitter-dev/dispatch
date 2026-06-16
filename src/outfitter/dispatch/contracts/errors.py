"""The ``DispatchError`` taxonomy and its single per-surface projection table
(ADR-0001).

Handlers raise typed ``DispatchError`` subclasses; they never catch-and-format.
Each surface catches at its boundary and projects via ``project_error`` —
CLI → exit code, MCP → ``isError`` + ``_meta`` code, remote → JSON-RPC code.
Unknown/native exceptions project to a generic internal error. Keeping every
code in one place is what makes the surfaces stay coherent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from outfitter.dispatch.client.errors import ClientError


class DispatchError(Exception):
    """Base for all dispatch errors. Carries the taxonomy each surface projects."""

    code: ClassVar[str] = "internal"
    exit_code: ClassVar[int] = 1
    rpc_code: ClassVar[int] = 1000

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(DispatchError):
    """Input failed validation at the boundary."""

    code = "validation"
    exit_code = 2
    rpc_code = 1002


class NotFoundError(DispatchError):
    """A referenced lane/trigger does not exist."""

    code = "not_found"
    exit_code = 4
    rpc_code = 1004


class LaneBusyError(DispatchError):
    """The lane is mid-turn and the op's guard declined to race it."""

    code = "lane_busy"
    exit_code = 5
    rpc_code = 1005


class ApprovalRequiredError(DispatchError):
    """The action needs an approval decision that no trigger/policy supplied."""

    code = "approval_required"
    exit_code = 6
    rpc_code = 1006


class AuthorityError(DispatchError):
    """A blocked write was attempted on an attached lane (ADR-0005/0018)."""

    code = "authority"
    exit_code = 7
    rpc_code = 1007


class AppServerError(DispatchError):
    """The App Server failed or rejected an operation."""

    code = "app_server"
    exit_code = 8
    rpc_code = 1008


class StagingError(DispatchError):
    """A launch packet could not be staged to disk (after the lane was registered)."""

    code = "staging"
    exit_code = 9
    rpc_code = 1009


@dataclass(frozen=True)
class ErrorProjection:
    """The transport-independent shape every surface renders."""

    code: str
    message: str
    exit_code: int
    rpc_code: int


def project_error(exc: BaseException) -> ErrorProjection:
    """Project any exception to the surface-independent error shape.

    ``DispatchError`` projects via its taxonomy; client-layer errors map to
    ``AppServerError``; anything else is a generic internal error.
    """
    if isinstance(exc, DispatchError):
        return ErrorProjection(exc.code, exc.message, exc.exit_code, exc.rpc_code)
    if isinstance(exc, ClientError):
        return ErrorProjection(
            AppServerError.code, str(exc), AppServerError.exit_code, AppServerError.rpc_code
        )
    return ErrorProjection("internal", str(exc) or exc.__class__.__name__, 1, 1000)
