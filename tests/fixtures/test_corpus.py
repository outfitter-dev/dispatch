"""Tests that keep the fixture corpus executable and honest."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from outfitter.dispatch.client.events import (
    LaneIdle,
    TurnFailed,
    TurnStarted,
    project_notification,
)
from outfitter.dispatch.client.models import (
    ConfigInfo,
    ModelListResult,
    ThreadInfo,
    ThreadListResult,
)
from outfitter.dispatch.core.sync import SyncLimits, scan_codex_jsonl

from . import copy_fixture, load_json, load_jsonl


def test_app_server_protocol_fixtures_validate_against_wire_models() -> None:
    config_payload = load_json("app_server", "config_read", "current.json")["config"]
    config = ConfigInfo.model_validate(config_payload)
    catalog = ModelListResult.model_validate(load_json("app_server", "model_list", "current.json"))
    legacy_catalog = ModelListResult.model_validate(
        load_json("app_server", "model_list", "legacy_additional_speed_tiers.json")
    )
    thread_list = ThreadListResult.model_validate(
        load_json("app_server", "thread_list", "basic.json")
    )
    thread_payload = load_json("app_server", "thread_read", "with_turns.json")["thread"]
    thread = ThreadInfo.model_validate(thread_payload)

    assert config.model == "gpt-5.5"
    assert config.service_tier == "fast"
    assert catalog.data[0].supported_reasoning_efforts == ["low", "medium", "high", "xhigh"]
    assert catalog.data[0].service_tiers[0].id == "priority"
    assert legacy_catalog.data[0].additional_speed_tiers == ["fast"]
    assert thread_list.data[0].model == "gpt-5.5"
    assert thread_list.next_cursor == "cursor-1"
    assert thread.turns[0]["id"] == "turn-1"


def test_app_server_event_fixture_projects_to_normalized_events() -> None:
    projected = []
    for message in load_jsonl("app_server", "events", "turn_failure_unsupported_model.jsonl"):
        method = message.get("method")
        params = message.get("params")
        assert isinstance(method, str)
        assert isinstance(params, dict)
        projected.extend(project_notification(method, cast(dict[str, object], params)))

    assert projected[0] == TurnStarted("019f0000-0000-7000-9000-000000000001", "turn-1")
    assert projected[1] == TurnFailed(
        "019f0000-0000-7000-9000-000000000001",
        "turn-1",
        "unsupported model: gpt-5.5-codex",
    )
    assert isinstance(projected[-1], LaneIdle)


def test_transcript_fixtures_scan_minimal_complete_case(tmp_path: Path) -> None:
    path = copy_fixture("transcripts", "minimal.jsonl", to=tmp_path / "minimal.jsonl")

    facts = scan_codex_jsonl(str(path), full=True)

    assert facts.state == "complete"
    assert facts.line_count == 3
    assert facts.session_id == "019f0000-0000-7000-9000-000000000001"
    assert facts.cwd == "/fixture/repo"
    assert facts.model == "gpt-5.5"
    assert facts.reasoning_effort == "xhigh"
    assert facts.latest_turn_id == "turn-1"


def test_transcript_fixture_preserves_top_identity_and_tail_recency(tmp_path: Path) -> None:
    path = copy_fixture(
        "transcripts",
        "long_history_top_and_tail.jsonl",
        to=tmp_path / "long_history_top_and_tail.jsonl",
    )

    facts = scan_codex_jsonl(
        str(path),
        limits=SyncLimits(top_bytes=512, tail_bytes=256, tail_lines=1),
    )

    assert facts.state == "partial"
    assert facts.session_id == "019f0000-0000-7000-9000-000000000010"
    assert facts.cwd == "/fixture/long"
    assert facts.source_kind == "cli"
    assert facts.model_provider == "openai"
    assert facts.latest_turn_id == "turn-9"
    assert facts.latest_event_at == "2026-06-11T12:00:05.000Z"


def test_transcript_fixture_ignores_malformed_jsonl_records(tmp_path: Path) -> None:
    path = copy_fixture(
        "transcripts",
        "malformed_lines.jsonl",
        to=tmp_path / "malformed_lines.jsonl",
    )

    facts = scan_codex_jsonl(str(path), full=True)

    assert facts.state == "complete"
    assert facts.line_count == 6
    assert facts.session_id == "019f0000-0000-7000-9000-000000000099"
    assert facts.model == "gpt-5.3-codex-spark"
    assert facts.reasoning_effort == "low"
    assert facts.latest_turn_id == "turn-malformed"
