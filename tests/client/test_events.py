"""Unit tests for LaneEvent projection (ADR-0007)."""

from __future__ import annotations

from outfitter.dispatch.client.events import (
    AccountRateLimitsUpdated,
    ApprovalRequested,
    GoalCleared,
    GoalUpdated,
    ItemCompleted,
    ItemStarted,
    LaneIdle,
    ServerRequestReceived,
    StatusChanged,
    ThreadArchived,
    ThreadCompacted,
    ThreadDeleted,
    ThreadStarted,
    ThreadUnarchived,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    classify_server_request,
    project_account_notification,
    project_notification,
    project_server_request,
    project_server_request_received,
)
from tests.fixtures import load_json


def test_turn_started_and_completed_carry_lane_and_turn() -> None:
    started = project_notification("turn/started", {"threadId": "L1", "turnId": "T1"})
    assert started == [TurnStarted("L1", "T1")]
    completed = project_notification("turn/completed", {"threadId": "L1", "turnId": "T1"})
    assert completed == [TurnCompleted("L1", "T1")]


def test_turn_failed_carries_message() -> None:
    failed = project_notification(
        "turn/failed", {"threadId": "L1", "turnId": "T1", "message": "unsupported model"}
    )
    assert failed == [TurnFailed("L1", "T1", "unsupported model")]
    assert failed[0].raw_payload == {
        "method": "turn/failed",
        "params": {"threadId": "L1", "turnId": "T1", "message": "unsupported model"},
    }


def test_item_completed_carries_item_id() -> None:
    assert project_notification("item/completed", {"threadId": "L1", "itemId": "I9"}) == [
        ItemCompleted("L1", "I9")
    ]


def test_canonical_item_notifications_carry_full_item_and_turn() -> None:
    item: dict[str, object] = {"id": "I9", "type": "agentMessage", "text": "done"}
    assert project_notification(
        "item/started", {"threadId": "L1", "turnId": "T1", "item": item}
    ) == [ItemStarted("L1", "I9", "T1", item)]
    assert project_notification(
        "item/completed", {"threadId": "L1", "turnId": "T1", "item": item}
    ) == [ItemCompleted("L1", "I9", "T1", item)]


def test_goal_and_compaction_notifications_project_to_activity_events() -> None:
    assert project_notification("thread/goal/updated", {"threadId": "L1"}) == [GoalUpdated("L1")]
    assert project_notification("thread/goal/cleared", {"threadId": "L1"}) == [GoalCleared("L1")]
    assert project_notification("thread/compacted", {"threadId": "L1"}) == [ThreadCompacted("L1")]


def test_archive_notifications_project_to_lifecycle_events() -> None:
    assert project_notification("thread/archived", {"threadId": "L1"}) == [ThreadArchived("L1")]
    assert project_notification("thread/unarchived", {"threadId": "L1"}) == [ThreadUnarchived("L1")]
    assert project_notification("thread/deleted", {"threadId": "L1"}) == [ThreadDeleted("L1")]


def test_account_rate_limit_notification_projects_without_lane_identity() -> None:
    event = project_account_notification(
        "account/rateLimits/updated",
        {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 55}}},
    )

    assert isinstance(event, AccountRateLimitsUpdated)
    assert event.rate_limits.limit_id == "codex"
    assert event.rate_limits.primary is not None
    assert event.rate_limits.primary.used_percent == 55
    assert project_account_notification("account/updated", {}) is None


def test_thread_started_projects_nested_full_thread() -> None:
    events = project_notification(
        "thread/started",
        {
            "thread": {
                "id": "child",
                "parentThreadId": "parent",
                "agentNickname": "Hypatia",
                "agentRole": "worker",
            }
        },
    )
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, ThreadStarted)
    assert event.lane_id == "child"
    assert event.thread is not None
    assert event.thread.parent_thread_id == "parent"


def test_status_changed_with_no_flags_also_emits_idle() -> None:
    events = project_notification(
        "thread/status/changed", {"threadId": "L1", "status": {"activeFlags": []}}
    )
    assert events == [StatusChanged("L1", ()), LaneIdle("L1")]


def test_status_changed_waiting_on_approval_does_not_emit_idle() -> None:
    events = project_notification(
        "thread/status/changed",
        {"threadId": "L1", "status": {"activeFlags": ["waitingOnApproval"]}},
    )
    assert events == [StatusChanged("L1", ("waitingOnApproval",))]


def test_unknown_method_and_missing_thread_id_project_to_nothing() -> None:
    assert project_notification("hook/started", {"threadId": "L1"}) == []
    assert project_notification("turn/completed", {"turnId": "T1"}) == []


def test_command_approval_request_projects_with_request_id() -> None:
    event = project_server_request(
        42,
        "item/commandExecution/requestApproval",
        {"threadId": "L1", "itemId": "I1", "turnId": "T1"},
    )
    assert event == ApprovalRequested("L1", 42, "command", "I1", "T1")
    assert event is not None
    assert event.raw_payload == {
        "id": 42,
        "method": "item/commandExecution/requestApproval",
        "params": {"threadId": "L1", "itemId": "I1", "turnId": "T1"},
    }


def test_file_change_approval_request_projects_as_file_change() -> None:
    event = project_server_request(
        7, "item/fileChange/requestApproval", {"threadId": "L1", "itemId": "I2"}
    )
    assert event == ApprovalRequested("L1", 7, "file_change", "I2", None)


def test_non_approval_server_request_projects_to_none() -> None:
    assert project_server_request(1, "item/tool/requestUserInput", {"threadId": "L1"}) is None


def test_server_request_categories_cover_the_protocol_manifest_and_unknown_methods() -> None:
    manifest = load_json("app_server", "protocol_manifest", "current.json")
    methods = manifest["server_requests"]
    assert isinstance(methods, list)
    assert all(isinstance(method, str) for method in methods)
    categories = {
        method: classify_server_request(method) for method in methods if isinstance(method, str)
    }
    assert categories == {
        "account/chatgptAuthTokens/refresh": "auth",
        "applyPatchApproval": "approval",
        "attestation/generate": "attestation",
        "execCommandApproval": "approval",
        "item/commandExecution/requestApproval": "approval",
        "item/fileChange/requestApproval": "approval",
        "item/permissions/requestApproval": "approval",
        "item/tool/call": "tool_call",
        "item/tool/requestUserInput": "user_input",
        "mcpServer/elicitation/request": "elicitation",
    }
    assert classify_server_request("future/request") == "unknown"


def test_generic_server_request_preserves_threadless_and_legacy_request_shapes() -> None:
    auth = project_server_request_received(
        "auth-7",
        "account/chatgptAuthTokens/refresh",
        {"audience": "app-server"},
    )
    assert auth == ServerRequestReceived(
        method="account/chatgptAuthTokens/refresh",
        request_id="auth-7",
        category="auth",
    )
    assert auth.lane_id is None
    assert auth.raw_params == {"audience": "app-server"}

    legacy = project_server_request_received(
        "approval-9",
        "item/fileChange/requestApproval",
        {"conversationId": "legacy-1", "itemId": "I1", "turnId": "T1"},
    )
    assert legacy.conversation_id == "legacy-1"
    assert legacy.thread_id is None
    assert legacy.lane_id == "legacy-1"
    assert project_server_request(
        "approval-9",
        "item/fileChange/requestApproval",
        {"conversationId": "legacy-1", "itemId": "I1", "turnId": "T1"},
    ) == ApprovalRequested("legacy-1", "approval-9", "file_change", "I1", "T1")
