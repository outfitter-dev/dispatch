#!/usr/bin/env python3
"""Assert content-free Claude probe structures and hook logs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

Event = dict[str, Any]


def load(path: Path) -> list[Event]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def responses(events: list[Event], hook: str) -> list[Event]:
    return [
        event
        for event in events
        if event.get("subtype") == "hook_response" and event.get("hook_event") == hook
    ]


def assert_hooks_paired(events: list[Event]) -> None:
    started = {event["hook_id"] for event in events if event.get("subtype") == "hook_started"}
    finished = {event["hook_id"] for event in events if event.get("subtype") == "hook_response"}
    assert started
    assert started == finished


def is_provider_activity(event: Event) -> bool:
    return event.get("type") == "assistant" or (
        event.get("subtype") == "hook_started"
        and event.get("hook_event") in {"PreToolUse", "PostToolUse"}
    )


def assert_processing(events: list[Event]) -> None:
    assert_hooks_paired(events)
    prompt = responses(events, "UserPromptSubmit")
    assert prompt
    assert all(event.get("exit_code") != 2 for event in prompt)
    assert not any(event.get("blocking_decision") for event in prompt)
    prompt_settled = max(event["sequence"] for event in prompt)
    assert any(
        is_provider_activity(event) and event["sequence"] > prompt_settled for event in events
    )


def assert_message_completed(events: list[Event]) -> int:
    assert_processing(events)
    activities = [event["sequence"] for event in events if is_provider_activity(event)]
    last_activity = max(activities)
    final_stops = [
        event for event in responses(events, "Stop") if event["sequence"] > last_activity
    ]
    assert final_stops
    assert all(event.get("exit_code") != 2 for event in final_stops)
    assert not any(event.get("blocking_decision") for event in final_stops)
    stop_settled = max(event["sequence"] for event in final_stops)
    results = [
        event
        for event in events
        if event.get("type") == "result"
        and event.get("subtype") == "success"
        and not event.get("is_error", False)
        and event["sequence"] > stop_settled
    ]
    assert results
    return max(event["sequence"] for event in results)


def assert_completed(events: list[Event]) -> None:
    result_sequence = assert_message_completed(events)
    assert any(
        event.get("type") == "process_exit"
        and event.get("exit_code") == 0
        and event["sequence"] > result_sequence
        for event in events
    )


def assert_structure(mode: str, events: list[Event]) -> None:
    if mode == "receipt":
        assert_completed(events)
    elif mode == "message-receipt":
        result_sequence = assert_message_completed(events)
        assert not any(event.get("type") == "process_exit" for event in events)
        assert result_sequence == max(event["sequence"] for event in events)
    elif mode == "block-prompt":
        assert_hooks_paired(events)
        assert any(event.get("exit_code") == 2 for event in responses(events, "UserPromptSubmit"))
        assert not any(event.get("type") == "assistant" for event in events)
        assert not responses(events, "Stop")
    elif mode == "continue-stop":
        stops = responses(events, "Stop")
        assert len(stops) >= 2
        assert any(event.get("blocking_decision") for event in stops[:-1])
        assert_completed(events)
    elif mode == "fail":
        assert any(event.get("exit_code") == 1 for event in responses(events, "UserPromptSubmit"))
        assert_completed(events)
    elif mode == "timeout":
        assert any(
            event.get("outcome") == "cancelled" for event in responses(events, "UserPromptSubmit")
        )
        assert_completed(events)
    elif mode == "preflight":
        assert_hooks_paired(events)
        assert any(
            event.get("dispatch_preflight") and event.get("exit_code") == 0
            for event in responses(events, "SessionStart")
        )
        assert not any(event.get("type") in {"assistant", "result"} for event in events)
        assert any(
            event.get("type") == "process_exit" and event.get("exit_code") == 0 for event in events
        )
    elif mode == "interrupt":
        assert_processing(events)
        assert not responses(events, "Stop")
        assert not any(
            event.get("type") == "result" and event.get("subtype") == "success" for event in events
        )
        assert any(
            event.get("type") == "process_exit" and event.get("exit_code") == 130
            for event in events
        )
    else:
        raise SystemExit(f"unknown structure mode: {mode}")


def assert_duplicate(paths: list[Path]) -> None:
    assert len(paths) == 3
    hook_events = load(paths[0])
    prompt_ids = [
        event["prompt_id"]
        for event in hook_events
        if event.get("hook_event_name") == "UserPromptSubmit"
        and event.get("prompt_marker") == "duplicate"
    ]
    assert len(prompt_ids) == 2 and len(set(prompt_ids)) == 2
    assert_completed(load(paths[1]))
    assert_completed(load(paths[2]))


def assert_aggregate_fixture(events: list[Event]) -> None:
    retries = [event for event in events if event.get("replayed")]
    assert len(retries) == 1
    original = next(
        event
        for event in events
        if event.get("source_delivery_id") == retries[0]["source_delivery_id"]
        and not event.get("replayed")
    )
    assert original["ingest_id"] != retries[0]["ingest_id"]
    stop_sources = {
        event["source_delivery_id"] for event in events if event.get("hook_event") == "Stop"
    }
    assert len(stop_sources) == 2


def assert_negative_fixtures(directory: Path) -> None:
    for name in (
        "truncated-hooks",
        "final-continuation",
        "stop-exit-two",
        "nonzero-exit",
    ):
        try:
            assert_completed(load(directory / f"{name}.jsonl"))
        except AssertionError:
            continue
        raise AssertionError(f"negative fixture unexpectedly completed: {name}")


def assert_coexistence_fixture(events: list[Event]) -> None:
    by_case: dict[str, list[Event]] = {}
    for event in events:
        by_case.setdefault(event["case"], []).append(event)

    ordinary = by_case["ordinary_tui"]
    assert any(
        event.get("event") == "shared_coherent_history" and event.get("value") is False
        for event in ordinary
    )
    assert any(
        event.get("actor") == "external_resume" and event.get("event") == "turn_completed"
        for event in ordinary
    )
    assert any(
        event.get("actor") == "attached_tui"
        and event.get("event") == "marker_visible"
        and event.get("marker") == "external"
        and event.get("value") is False
        for event in ordinary
    )
    assert any(
        event.get("actor") == "fresh_resume"
        and event.get("event") == "marker_visible"
        and event.get("marker") == "external"
        and event.get("value") is False
        for event in ordinary
    )

    agent_view = by_case["agent_view"]
    assert any(
        event.get("event") == "shared_coherent_history" and event.get("value") is False
        for event in agent_view
    )
    assert any(
        event.get("actor") == "external_resume"
        and event.get("event") == "resume_rejected"
        and event.get("exit_code") == 1
        and event.get("owner_alive") is True
        for event in agent_view
    )
    assert any(
        event.get("actor") == "attached_tui"
        and event.get("event") == "resume_rejected"
        and event.get("exit_code") == 1
        and event.get("owner_alive") is True
        for event in agent_view
    )
    assert any(event.get("event") == "attached_human_turn_completed" for event in agent_view)
    assert any(event.get("event") == "resume_completed_after_owner_stop" for event in agent_view)

    stream_owner = by_case["persistent_stream_owner"]
    assert any(
        event.get("event") == "shared_coherent_history" and event.get("value") is False
        for event in stream_owner
    )
    assert (
        len([event for event in stream_owner if event.get("event") == "owner_turn_completed"]) >= 2
    )
    assert any(
        event.get("actor") == "owner"
        and event.get("event") == "marker_visible"
        and event.get("marker") == "external"
        and event.get("value") is False
        for event in stream_owner
    )


def assert_cockpit_plan_fixture(events: list[Event]) -> None:
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["event"] for event in events] == [
        "target_resolved",
        "home_verified",
        "lease_acquired",
        "input_ack",
        "input_ack",
        "detail_identity_verified",
        "input_ack",
        "home_selection_reverified",
        "input_ack",
        "reply_prompt_verified",
        "input_ack",
        "lease_released",
        "lease_acquired",
        "concurrent_human_revision_changed",
        "ambiguous_target_rejected",
        "receipt_blocker",
    ]
    assert events[0].get("event") == "target_resolved"
    assert events[0].get("cockpit_scope") == "global_unscoped"
    assert events[0].get("cockpit_command") == "claude agents"
    assert events[0].get("roster_command") == "claude agents --json --all"
    assert events[0].get("target_join") == [
        "provider",
        "full_session_id",
        "cwd_worktree",
        "visible_row",
    ]
    assert events[0].get("cwd_worktree_verified") is True
    assert events[0].get("visible_row_match_count") == 1
    assert events[0].get("per_repo_cockpit") is False
    assert events[0].get("full_session_id_verified") is True

    required_guards = (
        "home_verified",
        "detail_identity_verified",
        "home_selection_reverified",
        "reply_prompt_verified",
    )
    for guard in required_guards:
        assert any(event.get("event") == guard and event.get("value") is True for event in events)
    assert events[1].get("target_row_visible") is True
    assert events[5].get("full_session_id_verified") is True
    assert events[5].get("title_verified") is True
    assert events[7].get("full_session_id_verified") is True
    assert events[9].get("target_identity_retained") is True

    first_lease = events[2]
    assert first_lease.get("exclusive") is True
    lease_id = first_lease["lease_id"]
    current_revision = first_lease["at_revision"]
    writes: list[Event] = []
    for event in events[3:12]:
        if snapshot_revision := event.get("snapshot_revision"):
            assert snapshot_revision > current_revision
            current_revision = snapshot_revision
        if event.get("event") == "input_ack":
            assert event.get("lease_id") == lease_id
            assert event.get("expected_revision") == current_revision
            assert event["result_revision"] > current_revision
            current_revision = event["result_revision"]
            writes.append(event)

    assert [event["input"] for event in writes] == [
        "named_key_down",
        "named_key_enter",
        "named_key_left",
        "literal_space",
        "message_submit",
    ]
    assert all(event.get("serialized") is True for event in writes)
    assert all(event.get("raw_input_logged") is False for event in writes)
    assert writes[-1].get("input") == "message_submit"
    assert writes[-1].get("atomic_batch") == ["payload", "enter"]
    assert events[11] == {
        "sequence": 12,
        "event": "lease_released",
        "lease_id": lease_id,
        "at_revision": current_revision,
    }

    forbidden_content_keys = {
        "prompt",
        "text",
        "payload",
        "raw",
        "stdout",
        "stderr",
        "log",
        "input_bytes",
        "transcript",
        "message",
    }

    def assert_content_free(value: Any) -> None:
        if isinstance(value, dict):
            assert not forbidden_content_keys.intersection(value)
            for nested in value.values():
                assert_content_free(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_content_free(nested)

    assert_content_free(events)

    assert events[14].get("event") == "ambiguous_target_rejected"
    assert events[14].get("visible_row_match_count") == 2
    assert events[14].get("action") == "abort_before_input"
    assert events[14].get("per_repo_fallback") is False

    second_lease = events[12]
    human_race = events[13]
    assert second_lease.get("exclusive") is True
    assert human_race.get("lease_id") == second_lease.get("lease_id")
    assert human_race.get("expected_revision") == second_lease.get("at_revision")
    assert human_race["actual_revision"] > human_race["expected_revision"]
    assert human_race.get("lease_invalidated") is True
    assert human_race.get("action") == "abort_before_payload"
    assert not any(
        event.get("event") == "input_ack" and event.get("lease_id") == second_lease.get("lease_id")
        for event in events
    )
    blocker = next(event for event in events if event.get("event") == "receipt_blocker")
    assert blocker.get("aggregate_hook_settlement") == "unproven"
    assert blocker.get("owned_provider_activity") == "unproven"
    assert blocker.get("capability") == "blocked"


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: assert_probe.py MODE PATH [PATH ...]")
    mode = sys.argv[1]
    paths = [Path(value) for value in sys.argv[2:]]
    if mode == "duplicate":
        assert_duplicate(paths)
    elif mode == "aggregate-fixture":
        assert_aggregate_fixture(load(paths[0]))
    elif mode == "negative-fixtures":
        assert_negative_fixtures(paths[0])
    elif mode == "coexistence-fixture":
        assert_coexistence_fixture(load(paths[0]))
    elif mode == "cockpit-plan-fixture":
        assert_cockpit_plan_fixture(load(paths[0]))
    else:
        assert len(paths) == 1
        assert_structure(mode, load(paths[0]))


if __name__ == "__main__":
    main()
