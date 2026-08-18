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
    AccountRateLimitsResult,
    AccountReadResult,
    AccountUsageResult,
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
    account = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    signed_out = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_out.json")
    )
    rate_limits = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "current.json")
    )
    usage = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "current.json")
    )
    partial_limits = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "partial.json")
    )
    partial_usage = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "partial.json")
    )

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
    assert account.account is not None and account.account.plan_type == "pro"
    assert signed_out.account is None
    assert rate_limits.rate_limit_reset_credits is not None
    assert rate_limits.rate_limits.spend_control_reached is False
    # Absent on the partial fixture: unavailable, not "not reached".
    assert partial_limits.rate_limits.spend_control_reached is None
    assert usage.summary.lifetime_tokens == 123456
    assert partial_limits.rate_limits.primary is None
    assert partial_usage.daily_usage_buckets is None


def test_app_server_protocol_manifest_tracks_v0147_capabilities() -> None:
    manifest = cast(dict[str, Any], load_json("app_server", "protocol_manifest", "current.json"))

    assert manifest["codex_cli_version"] == "0.147.0"
    assert len(manifest["client_requests"]["stable"]) == 95
    assert "account/usage/read" in manifest["client_requests"]["stable"]
    assert "thread/delete" in manifest["client_requests"]["stable"]
    assert "thread/items/list" in manifest["client_requests"]["experimental_only"]
    assert "thread/deleted" in manifest["server_notifications"]
    assert set(manifest["thread_item_types"]) == CODEX_ITEM_TYPES
    assert "supportsPersonality" in manifest["selected_shapes"]["model_fields"]
    assert {"cursor", "cwd", "limit"} == set(
        manifest["selected_shapes"]["permission_profile_list_fields"]
    )
    assert {"data", "nextCursor"} == set(
        manifest["selected_shapes"]["permission_profile_result_fields"]
    )
    assert {"allowed", "description", "id"} == set(
        manifest["selected_shapes"]["permission_profile_summary_fields"]
    )
    assert "permissions" in manifest["selected_shapes"]["thread_start_experimental_fields"]
    assert "permissions" in manifest["selected_shapes"]["turn_start_experimental_fields"]
    required_user_inputs = {"text", "image", "localImage"}
    assert required_user_inputs <= set(manifest["selected_shapes"]["turn_start_user_input_types"])
    assert required_user_inputs <= set(manifest["selected_shapes"]["turn_steer_user_input_types"])
    expected_image_details = {"auto", "low", "high", "original"}
    assert expected_image_details == set(manifest["selected_shapes"]["turn_start_image_details"])
    assert expected_image_details == set(manifest["selected_shapes"]["turn_steer_image_details"])
    assert set(manifest["selected_shapes"]["account_types"]) == {
        "amazonBedrock",
        "apiKey",
        "chatgpt",
    }
    assert "rateLimitsByLimitId" in manifest["selected_shapes"]["rate_limit_result_fields"]
    assert "rateLimitReachedType" in manifest["selected_shapes"]["rate_limit_snapshot_fields"]
    assert "expiresAt" in manifest["selected_shapes"]["reset_credit_fields"]
    assert "dailyUsageBuckets" in manifest["selected_shapes"]["usage_result_fields"]
    assert "lifetimeTokens" in manifest["selected_shapes"]["usage_summary_fields"]
    assert "lastTurnId" in manifest["selected_shapes"]["thread_fork_fields"]
    assert "backwardsCursor" in manifest["selected_shapes"]["thread_list_result_fields"]
    assert {
        "parentThreadId",
        "ancestorThreadId",
    } <= set(manifest["selected_shapes"]["thread_list_experimental_fields"])
    assert {"excludeTurns", "initialTurnsPage"} <= set(
        manifest["selected_shapes"]["thread_resume_experimental_fields"]
    )
    assert {"cursor", "itemsView", "sortDirection"} <= set(
        manifest["selected_shapes"]["thread_turns_list_fields"]
    )
    assert {"cursor", "turnId", "sortDirection"} <= set(
        manifest["selected_shapes"]["thread_items_list_fields"]
    )
    assert (
        "initialTurnsPage"
        in manifest["selected_shapes"]["thread_resume_result_experimental_fields"]
    )
    assert {"data", "nextCursor", "backwardsCursor"} == set(
        manifest["selected_shapes"]["thread_turns_page_fields"]
    )
    assert {"data", "nextCursor", "backwardsCursor"} == set(
        manifest["selected_shapes"]["thread_items_page_fields"]
    )


def test_app_server_protocol_manifest_records_v0147_additions() -> None:
    """Capabilities that arrived between the 0.144.0 and 0.147.0 pins.

    Dispatch does not consume the thread-section, app-registry, or audio-input
    surfaces yet; these assertions pin *when* they appeared so the follow-up work
    in ``docs/research/app-server-verification.md`` stays anchored to a version.
    """

    manifest = cast(dict[str, Any], load_json("app_server", "protocol_manifest", "current.json"))
    stable = set(manifest["client_requests"]["stable"])
    experimental = set(manifest["client_requests"]["experimental_only"])

    assert {
        "app/installed",
        "app/read",
        "thread/section/move",
        "threadSection/create",
        "threadSection/delete",
        "threadSection/list",
        "threadSection/update",
    } <= stable
    assert {"environment/status", "plugin/search", "thread/searchOccurrences"} <= experimental
    assert {"thread/environment/connected", "thread/environment/disconnected"} <= set(
        manifest["server_notifications"]
    )
    assert {"section", "sectionEnteredAt"} <= set(manifest["selected_shapes"]["thread_fields"])
    assert "sectionId" in manifest["selected_shapes"]["thread_list_experimental_fields"]
    assert "modelSpecialty" in manifest["selected_shapes"]["model_fields"]
    assert "spendControlReached" in manifest["selected_shapes"]["rate_limit_snapshot_fields"]
    assert {"itemsBackwardsCursor", "turnsBackwardsCursor"} <= set(
        manifest["selected_shapes"]["thread_resume_result_experimental_fields"]
    )
    for shape in ("turn_start_user_input_types", "turn_steer_user_input_types"):
        assert {"audio", "localAudio"} <= set(manifest["selected_shapes"][shape])


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
