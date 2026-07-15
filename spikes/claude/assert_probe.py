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


def assert_completed(events: list[Event]) -> None:
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
    result_sequence = max(event["sequence"] for event in results)
    assert any(
        event.get("type") == "process_exit"
        and event.get("exit_code") == 0
        and event["sequence"] > result_sequence
        for event in events
    )


def assert_structure(mode: str, events: list[Event]) -> None:
    if mode == "receipt":
        assert_completed(events)
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
    else:
        assert len(paths) == 1
        assert_structure(mode, load(paths[0]))


if __name__ == "__main__":
    main()
