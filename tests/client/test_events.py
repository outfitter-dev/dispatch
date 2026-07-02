"""Unit tests for LaneEvent projection (ADR-0007)."""

from __future__ import annotations

from outfitter.dispatch.client.events import (
    ApprovalRequested,
    GoalCleared,
    GoalUpdated,
    ItemCompleted,
    LaneIdle,
    StatusChanged,
    ThreadArchived,
    ThreadCompacted,
    ThreadUnarchived,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    project_notification,
    project_server_request,
)


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


def test_goal_and_compaction_notifications_project_to_activity_events() -> None:
    assert project_notification("thread/goal/updated", {"threadId": "L1"}) == [GoalUpdated("L1")]
    assert project_notification("thread/goal/cleared", {"threadId": "L1"}) == [GoalCleared("L1")]
    assert project_notification("thread/compacted", {"threadId": "L1"}) == [ThreadCompacted("L1")]


def test_archive_notifications_project_to_lifecycle_events() -> None:
    assert project_notification("thread/archived", {"threadId": "L1"}) == [ThreadArchived("L1")]
    assert project_notification("thread/unarchived", {"threadId": "L1"}) == [ThreadUnarchived("L1")]


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
