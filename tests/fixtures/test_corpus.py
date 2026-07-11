"""Tests that keep the fixture corpus executable and honest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
from outfitter.dispatch.core.codex_items import CODEX_ITEM_TYPES
from outfitter.dispatch.core.history import summarize_history
from outfitter.dispatch.core.sync import SyncLimits, scan_codex_jsonl
from outfitter.dispatch.registry.models import Lane, ProviderEvent

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
    descendants = ThreadListResult.model_validate(
        load_json("app_server", "thread_list", "descendants_v0144.json")
    )
    thread_payload = load_json("app_server", "thread_read", "with_turns.json")["thread"]
    thread = ThreadInfo.model_validate(thread_payload)

    assert config.model == "gpt-5.5"
    assert config.service_tier == "fast"
    assert catalog.data[0].supported_reasoning_efforts == ["low", "medium", "high", "xhigh"]
    assert catalog.data[0].service_tiers[0].id == "priority"
    assert catalog.data[0].input_modalities == ["text", "image"]
    assert catalog.data[0].supports_personality is True
    assert catalog.data[2].supported_reasoning_efforts == ["low", "max", "ultra"]
    assert legacy_catalog.data[0].additional_speed_tiers == ["fast"]
    assert thread_list.data[0].model == "gpt-5.5"
    assert thread_list.next_cursor == "cursor-1"
    assert descendants.data[0].source_kind == "subAgentThreadSpawn"
    assert descendants.data[1].source_kind == "subAgentReview"
    assert descendants.data[1].parent_thread_id == descendants.data[0].id
    assert thread.turns[0]["id"] == "turn-1"


def test_app_server_protocol_manifest_tracks_v0144_capabilities() -> None:
    manifest = cast(dict[str, Any], load_json("app_server", "protocol_manifest", "current.json"))

    assert manifest["codex_cli_version"] == "0.144.0"
    assert len(manifest["client_requests"]["stable"]) == 87
    assert "account/usage/read" in manifest["client_requests"]["stable"]
    assert "thread/delete" in manifest["client_requests"]["stable"]
    assert "thread/items/list" in manifest["client_requests"]["experimental_only"]
    assert "thread/deleted" in manifest["server_notifications"]
    assert set(manifest["thread_item_types"]) == CODEX_ITEM_TYPES
    assert "supportsPersonality" in manifest["selected_shapes"]["model_fields"]
    assert "lastTurnId" in manifest["selected_shapes"]["thread_fork_fields"]
    assert "backwardsCursor" in manifest["selected_shapes"]["thread_list_result_fields"]
    assert {
        "parentThreadId",
        "ancestorThreadId",
    } <= set(manifest["selected_shapes"]["thread_list_experimental_fields"])


def test_history_v2_fixture_summarizes_tools_files_and_subagents() -> None:
    payload = load_json("app_server", "thread_read", "history_v2.json")
    lane = Lane(
        id="019f0000-0000-7000-9000-000000000042",
        ref="0Hist1",
        ref_source="fixture",
        ref_payload="history",
        ref_mixer="fixture",
        handle="@history",
        source="own",
        status="idle",
        cwd="/fixture/history",
        created_at=datetime(2026, 6, 16, tzinfo=UTC),
        updated_at=datetime(2026, 6, 16, tzinfo=UTC),
    )

    summary, items, tools, files = summarize_history(payload, lane=lane, sync=None)

    assert summary.turns == 1
    assert summary.messages == 2
    assert summary.tool_calls == 1
    assert summary.unique_tools == ["bash"]
    assert summary.subagent_thread_ids == ["019f0000-0000-7000-9000-000000000099"]
    assert [item.type for item in items] == [
        "userMessage",
        "toolCall",
        "changes",
        "agentMessage",
    ]
    assert [tool.tool for tool in tools] == ["bash"]
    assert [file.path for file in files] == [
        "README.md",
        "src/outfitter/dispatch/core/history.py",
    ]


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


def test_provider_event_replay_fixture_validates_storage_shape() -> None:
    events = [
        ProviderEvent.model_validate(row)
        for row in load_jsonl("provider_events", "codex_turn_lifecycle.jsonl")
    ]

    assert [event.event_type for event in events] == ["turn/started", "turn/completed"]
    assert [event.provider_turn_id for event in events] == ["turn-1", "turn-1"]
    assert events[0].payload is not None
    assert events[0].payload["method"] == "turn/started"


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
