"""Pure response policy for App Server interactive requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from outfitter.dispatch.client.events import ServerRequestReceived
from outfitter.dispatch.client.models import JsonRpcError
from outfitter.dispatch.config import InteractiveRequestMode
from outfitter.dispatch.contracts.errors import ValidationError

TerminalState = Literal["responded", "denied", "timed_out", "failed"]

_POLICY_DENIED = -32040
_UNSUPPORTED = -32041
_TIMED_OUT = -32042


@dataclass(frozen=True)
class PlannedResponse:
    result: Mapping[str, object] | None = None
    error: JsonRpcError | None = None
    state: TerminalState = "responded"
    summary: str = "responded"


def automatic_response(
    request: ServerRequestReceived,
    *,
    mode: InteractiveRequestMode,
    actionable: bool,
) -> PlannedResponse | None:
    """Return an immediate safe response, or ``None`` for operator attention."""

    if request.category in {"auth", "attestation", "unknown"}:
        return _error_response(
            _UNSUPPORTED,
            "Dispatch has no registered host handler for this request",
            state="failed",
            summary="unsupported host request",
        )
    if mode == "deny" or not actionable:
        return denied_response(request.method)
    if mode == "permissive":
        if request.method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return PlannedResponse(result={"decision": "accept"}, summary="policy accepted")
        if request.method in {"execCommandApproval", "applyPatchApproval"}:
            return PlannedResponse(result={"decision": "approved"}, summary="policy approved")
        if request.method == "item/permissions/requestApproval":
            permissions = request.raw_params.get("permissions")
            if isinstance(permissions, dict):
                return PlannedResponse(
                    result={"permissions": permissions, "scope": "turn"},
                    summary="policy granted requested permissions for turn",
                )
            return _error_response(
                _UNSUPPORTED,
                "permission request did not include a grantable profile",
                state="failed",
                summary="malformed permission request",
            )
    return None


def denied_response(method: str) -> PlannedResponse:
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return PlannedResponse(
            result={"decision": "decline"}, state="denied", summary="policy declined"
        )
    if method in {"execCommandApproval", "applyPatchApproval"}:
        return PlannedResponse(
            result={"decision": "denied"}, state="denied", summary="policy denied"
        )
    if method == "mcpServer/elicitation/request":
        return PlannedResponse(
            result={"action": "decline"}, state="denied", summary="policy declined elicitation"
        )
    return _error_response(
        _POLICY_DENIED,
        "Dispatch policy denied this interactive request",
        state="denied",
        summary="policy denied",
    )


def timeout_response(method: str) -> PlannedResponse:
    if method in {"execCommandApproval", "applyPatchApproval"}:
        return PlannedResponse(
            result={"decision": "timed_out"},
            state="timed_out",
            summary="attention timed out",
        )
    if method == "mcpServer/elicitation/request":
        return PlannedResponse(
            result={"action": "cancel"},
            state="timed_out",
            summary="elicitation timed out",
        )
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return PlannedResponse(
            result={"decision": "decline"},
            state="timed_out",
            summary="approval timed out",
        )
    return _error_response(
        _TIMED_OUT,
        "Dispatch operator response timed out",
        state="timed_out",
        summary="attention timed out",
    )


def validate_operator_response(method: str, response: Mapping[str, object]) -> dict[str, object]:
    result = dict(response)
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        _require_keys(result, {"decision"})
        if result.get("decision") not in {"accept", "acceptForSession", "decline", "cancel"}:
            raise ValidationError("approval response requires a supported decision")
    elif method in {"execCommandApproval", "applyPatchApproval"}:
        _require_keys(result, {"decision"})
        if result.get("decision") not in {
            "approved",
            "approved_for_session",
            "denied",
            "timed_out",
            "abort",
        }:
            raise ValidationError("legacy approval response requires a supported decision")
    elif method == "item/permissions/requestApproval":
        _require_keys(result, {"permissions"}, optional={"scope", "strictAutoReview"})
        permissions = result.get("permissions")
        if not isinstance(permissions, dict):
            raise ValidationError("permission response requires a permissions object")
        if not set(permissions) <= {"fileSystem", "network"}:
            raise ValidationError("permission response contains unknown permission fields")
        if result.get("scope", "turn") not in {"turn", "session"}:
            raise ValidationError("permission response scope must be turn or session")
        strict_review = result.get("strictAutoReview")
        if strict_review is not None and not isinstance(strict_review, bool):
            raise ValidationError("strictAutoReview must be a boolean")
    elif method == "item/tool/requestUserInput":
        _require_keys(result, {"answers"})
        answers = result.get("answers")
        if not isinstance(answers, dict):
            raise ValidationError("user-input response requires an answers object")
        for question_id, answer in answers.items():
            if not isinstance(question_id, str) or not isinstance(answer, dict):
                raise ValidationError("user-input answers must map question ids to objects")
            if set(answer) != {"answers"} or not isinstance(answer.get("answers"), list):
                raise ValidationError("each user-input answer requires an answers list")
            if not all(isinstance(value, str) for value in answer["answers"]):
                raise ValidationError("user-input answer values must be strings")
    elif method == "mcpServer/elicitation/request":
        _require_keys(result, {"action"}, optional={"content", "_meta"})
        if result.get("action") not in {"accept", "decline", "cancel"}:
            raise ValidationError("elicitation response requires accept, decline, or cancel")
    elif method == "item/tool/call":
        _require_keys(result, {"contentItems", "success"})
        content_items = result.get("contentItems")
        if not isinstance(content_items, list) or not isinstance(result.get("success"), bool):
            raise ValidationError("tool response requires contentItems and success")
        for item in content_items:
            if not isinstance(item, dict):
                raise ValidationError("tool response contentItems must be objects")
            item_type = item.get("type")
            required_value = "text" if item_type == "inputText" else "imageUrl"
            if item_type not in {"inputText", "inputImage"} or set(item) != {
                "type",
                required_value,
            }:
                raise ValidationError("tool response content item has an invalid shape")
            if not isinstance(item.get(required_value), str):
                raise ValidationError("tool response content values must be strings")
    else:
        raise ValidationError("this request category requires a registered host handler")
    return result


def _require_keys(
    result: Mapping[str, object],
    required: set[str],
    *,
    optional: set[str] | None = None,
) -> None:
    keys = set(result)
    if not required <= keys or not keys <= required | (optional or set()):
        raise ValidationError("interactive response fields do not match the request schema")


def expected_response(method: str) -> dict[str, object] | None:
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return {"decision": "accept | acceptForSession | decline | cancel"}
    if method in {"execCommandApproval", "applyPatchApproval"}:
        return {"decision": "approved | approved_for_session | denied | abort"}
    if method == "item/permissions/requestApproval":
        return {"permissions": {}, "scope": "turn | session"}
    if method == "item/tool/requestUserInput":
        return {"answers": {"<question-id>": {"answers": ["<answer>"]}}}
    if method == "mcpServer/elicitation/request":
        return {"action": "accept | decline | cancel", "content": {}}
    if method == "item/tool/call":
        return {"contentItems": [], "success": True}
    return None


def _error_response(
    code: int,
    message: str,
    *,
    state: TerminalState,
    summary: str,
) -> PlannedResponse:
    return PlannedResponse(
        error=JsonRpcError(code=code, message=message),
        state=state,
        summary=summary,
    )
