"""Unit tests for the wire models: alias round-tripping + the verified gotchas."""

from __future__ import annotations

import pytest

from outfitter.dispatch.client.models import (
    AccountRateLimitsResult,
    AccountReadResult,
    AccountUsageResult,
    AppModel,
    ConfigInfo,
    InitializeResult,
    JsonRpcError,
    ModelListParams,
    ModelListResult,
    ModelServiceTier,
    SandboxPolicy,
    TextInput,
    ThreadCompactStartParams,
    ThreadForkParams,
    ThreadGoal,
    ThreadGoalSetParams,
    ThreadInfo,
    ThreadItemsListParams,
    ThreadItemsPage,
    ThreadListParams,
    ThreadListResult,
    ThreadReadParams,
    ThreadResumeInitialTurnsPageParams,
    ThreadResumeParams,
    ThreadResumeResult,
    ThreadRollbackParams,
    ThreadStartParams,
    ThreadTurnsListParams,
    ThreadTurnsPage,
    TurnStartParams,
    TurnSteerParams,
    is_json_rpc_id,
)
from tests.fixtures import load_json


def test_account_capacity_models_parse_current_and_signed_out_shapes() -> None:
    signed_in = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    signed_out = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_out.json")
    )
    limits = AccountRateLimitsResult.model_validate(
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

    assert signed_in.account is not None
    assert signed_in.account.plan_type == "pro"
    assert "accessToken" not in signed_in.account.model_dump(by_alias=True)
    assert signed_out.account is None
    assert limits.rate_limits_by_limit_id is not None
    assert limits.rate_limits_by_limit_id["codex"].secondary is not None
    assert limits.rate_limits_by_limit_id["codex"].secondary.used_percent == 40
    assert limits.rate_limit_reset_credits is not None
    assert limits.rate_limit_reset_credits.credits is not None
    assert limits.rate_limit_reset_credits.credits[0].status == "available"
    assert usage.summary.lifetime_tokens == 123456
    assert usage.daily_usage_buckets is not None
    assert usage.daily_usage_buckets[-1].tokens == 3400
    assert partial_limits.rate_limits.primary is None
    assert partial_usage.summary.lifetime_tokens is None


def test_thread_start_sandbox_is_string_enum() -> None:
    params = ThreadStartParams(cwd="/work", sandbox="workspace-write", ephemeral=True)
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped["sandbox"] == "workspace-write"  # STRING, not an object
    assert "approvalPolicy" not in dumped
    assert dumped["ephemeral"] is True


def test_json_rpc_ids_exclude_boolean_values_and_errors_use_wire_aliases() -> None:
    assert is_json_rpc_id(7)
    assert is_json_rpc_id("server-request-7")
    assert not is_json_rpc_id(True)
    assert not is_json_rpc_id(None)
    assert JsonRpcError(code=-32001, message="denied", data={"reason": "policy"}).model_dump(
        by_alias=True, exclude_none=True
    ) == {"code": -32001, "message": "denied", "data": {"reason": "policy"}}


def test_thread_start_omits_policy_fields_when_inheriting_codex_config() -> None:
    params = ThreadStartParams(cwd="/work")
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped == {"cwd": "/work", "ephemeral": False}


def test_thread_start_includes_rich_session_options() -> None:
    params = ThreadStartParams(
        cwd="/work",
        sandbox="workspace-write",
        approval_policy="on-request",
        approvals_reviewer="auto_review",
        base_instructions="base",
        developer_instructions="dev",
        personality="pragmatic",
        service_tier="priority",
        model="test-model",
        model_provider="openai",
    )
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped["approvalPolicy"] == "on-request"
    assert dumped["approvalsReviewer"] == "auto_review"
    assert dumped["baseInstructions"] == "base"
    assert dumped["developerInstructions"] == "dev"
    assert dumped["personality"] == "pragmatic"
    assert dumped["serviceTier"] == "priority"
    assert dumped["model"] == "test-model"
    assert dumped["modelProvider"] == "openai"


def test_turn_start_sandbox_policy_is_object_and_camelcased() -> None:
    params = TurnStartParams(
        thread_id="t1",
        input=[TextInput(text="hi")],
        cwd="/work",
        sandbox_policy=SandboxPolicy(type="readOnly"),
    )
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped["threadId"] == "t1"
    assert dumped["sandboxPolicy"] == {"type": "readOnly"}  # OBJECT, not a string
    assert dumped["input"] == [{"type": "text", "text": "hi"}]
    assert "effort" not in dumped  # None excluded


def test_turn_start_omits_policy_fields_when_inheriting_codex_config() -> None:
    params = TurnStartParams(thread_id="t1", input=[TextInput(text="hi")], cwd="/work")
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert "approvalPolicy" not in dumped
    assert "sandboxPolicy" not in dumped


def test_turn_start_includes_effort_when_set() -> None:
    params = TurnStartParams(thread_id="t1", input=[TextInput(text="hi")], cwd="/w", effort="low")
    assert params.model_dump(by_alias=True, exclude_none=True)["effort"] == "low"


def test_turn_start_accepts_model_defined_effort() -> None:
    params = TurnStartParams(thread_id="t1", input=[TextInput(text="hi")], cwd="/w", effort="ultra")
    assert params.model_dump(by_alias=True, exclude_none=True)["effort"] == "ultra"


def test_turn_start_includes_optional_overrides_when_set() -> None:
    params = TurnStartParams(
        thread_id="t1",
        input=[TextInput(text="hi")],
        cwd="/w",
        approvals_reviewer="user",
        effort="xhigh",
        summary="concise",
        model="test-model",
        service_tier="priority",
        personality="friendly",
        output_schema={"type": "object"},
    )
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped["approvalsReviewer"] == "user"
    assert dumped["effort"] == "xhigh"
    assert dumped["summary"] == "concise"
    assert dumped["model"] == "test-model"
    assert dumped["serviceTier"] == "priority"
    assert dumped["personality"] == "friendly"
    assert dumped["outputSchema"] == {"type": "object"}


def test_turn_steer_requires_expected_turn_id_alias() -> None:
    params = TurnSteerParams(thread_id="t1", expected_turn_id="turn-9", input=[TextInput(text="x")])
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped["expectedTurnId"] == "turn-9"


def test_thread_list_result_reads_data_key() -> None:
    result = ThreadListResult.model_validate(
        {"data": [{"id": "a"}, {"id": "b", "ephemeral": True}], "nextCursor": "c1"}
    )
    assert [t.id for t in result.data] == ["a", "b"]
    assert result.next_cursor == "c1"


def test_model_list_params_include_pagination_and_hidden_catalog() -> None:
    params = ModelListParams(cursor="next", include_hidden=True, limit=100)
    assert params.model_dump(by_alias=True, exclude_none=True) == {
        "cursor": "next",
        "includeHidden": True,
        "limit": 100,
    }


def test_thread_list_params_include_current_native_filters() -> None:
    params = ThreadListParams(
        limit=25,
        archived=False,
        cwd=["/repo", "/other"],
        search_term="schema drift",
        sort_direction="desc",
        sort_key="updated_at",
        source_kinds=["cli", "appServer"],
        parent_thread_id="parent",
        use_state_db_only=True,
    )
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped == {
        "limit": 25,
        "archived": False,
        "cwd": ["/repo", "/other"],
        "searchTerm": "schema drift",
        "sortDirection": "desc",
        "sortKey": "updated_at",
        "sourceKinds": ["cli", "appServer"],
        "parentThreadId": "parent",
        "useStateDbOnly": True,
    }


def test_thread_list_topology_filters_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        ThreadListParams(parent_thread_id="parent", ancestor_thread_id="root")


def test_thread_resume_can_request_low_hydration_subscription() -> None:
    params = ThreadResumeParams(
        thread_id="t1",
        exclude_turns=True,
        initial_turns_page=ThreadResumeInitialTurnsPageParams(
            limit=20, sort_direction="desc", items_view="summary"
        ),
    )
    assert params.model_dump(by_alias=True, exclude_none=True) == {
        "threadId": "t1",
        "excludeTurns": True,
        "initialTurnsPage": {
            "limit": 20,
            "sortDirection": "desc",
            "itemsView": "summary",
        },
    }


def test_resume_result_parses_full_response_and_bootstrap_page() -> None:
    result = ThreadResumeResult.model_validate(
        {
            "thread": {"id": "t1"},
            "cwd": "/repo",
            "model": "gpt-test",
            "modelProvider": "openai",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "sandbox": {"type": "workspaceWrite"},
            "initialTurnsPage": {
                "data": [
                    {
                        "id": "turn-2",
                        "status": "completed",
                        "items": [{"id": "item-2", "type": "agentMessage"}],
                        "itemsView": "summary",
                    }
                ],
                "nextCursor": "older",
                "backwardsCursor": "newer",
            },
        }
    )

    assert result.thread.id == "t1"
    assert result.initial_turns_page is not None
    assert result.initial_turns_page.data[0].items_view == "summary"
    assert result.initial_turns_page.next_cursor == "older"
    assert result.initial_turns_page.backwards_cursor == "newer"


def test_turn_and_item_page_models_use_wire_aliases() -> None:
    turns_params = ThreadTurnsListParams(
        thread_id="t1", cursor="older", limit=10, sort_direction="desc", items_view="full"
    )
    items_params = ThreadItemsListParams(
        thread_id="t1", cursor="items", limit=25, sort_direction="asc", turn_id="turn-2"
    )
    assert turns_params.model_dump(by_alias=True, exclude_none=True) == {
        "threadId": "t1",
        "cursor": "older",
        "limit": 10,
        "sortDirection": "desc",
        "itemsView": "full",
    }
    assert items_params.model_dump(by_alias=True, exclude_none=True) == {
        "threadId": "t1",
        "cursor": "items",
        "limit": 25,
        "sortDirection": "asc",
        "turnId": "turn-2",
    }

    turns = ThreadTurnsPage.model_validate(
        {"data": [], "nextCursor": "older", "backwardsCursor": "newer"}
    )
    items = ThreadItemsPage.model_validate(
        {"data": [{"id": "i1", "type": "userMessage"}], "backwardsCursor": "back"}
    )
    assert turns.next_cursor == "older"
    assert turns.backwards_cursor == "newer"
    assert items.data[0]["id"] == "i1"
    assert items.backwards_cursor == "back"


def test_thread_info_keeps_sync_metadata_fields() -> None:
    thread = ThreadInfo.model_validate(
        {
            "id": "t1",
            "sessionId": "t1",
            "parentThreadId": "parent",
            "path": "/tmp/rollout.jsonl",
            "modelProvider": "openai",
            "threadSource": "user",
            "agentNickname": "review-agent",
            "agentRole": "reviewer",
            "cliVersion": "0.144.0",
            "recencyAt": 124,
            "updatedAt": 123,
        }
    )

    assert thread.session_id == "t1"
    assert thread.parent_thread_id == "parent"
    assert thread.path == "/tmp/rollout.jsonl"
    assert thread.model_provider == "openai"
    assert thread.thread_source == "user"
    assert thread.agent_nickname == "review-agent"
    assert thread.agent_role == "reviewer"
    assert thread.cli_version == "0.144.0"
    assert thread.recency_at == 124
    assert thread.updated_at == 123


def test_thread_info_normalizes_subagent_source_union() -> None:
    thread = ThreadInfo.model_validate(
        {
            "id": "child",
            "source": {
                "subAgent": {
                    "thread_spawn": {
                        "depth": 2,
                        "parent_thread_id": "parent",
                        "agent_nickname": "Hypatia",
                        "agent_role": "worker",
                    }
                }
            },
        }
    )

    assert thread.source_kind == "subAgentThreadSpawn"
    assert thread.spawned_source == {
        "depth": 2,
        "parent_thread_id": "parent",
        "agent_nickname": "Hypatia",
        "agent_role": "worker",
    }


def test_thread_info_keeps_observed_model_service_tier() -> None:
    thread = ThreadInfo.model_validate(
        {
            "id": "t1",
            "modelProvider": "openai",
            "model": "gpt-5.5",
            "reasoningEffort": "xhigh",
            "serviceTier": "priority",
        }
    )

    assert thread.model_provider == "openai"
    assert thread.model == "gpt-5.5"
    assert thread.reasoning_effort == "xhigh"
    assert thread.service_tier == "priority"


def test_config_and_model_catalog_wire_models_accept_camel_case() -> None:
    config_payload = load_json("app_server", "config_read", "current.json")["config"]
    config = ConfigInfo.model_validate(config_payload)
    catalog = ModelListResult.model_validate(load_json("app_server", "model_list", "current.json"))

    assert config.model_provider == "openai"
    assert config.service_tier == "fast"
    assert catalog.data[0] == AppModel(
        id="gpt-5.5",
        model="gpt-5.5",
        display_name="GPT-5.5",
        description="Frontier model for complex coding, research, and real-world work.",
        is_default=True,
        hidden=False,
        default_reasoning_effort="medium",
        supported_reasoning_efforts=["low", "medium", "high", "xhigh"],
        input_modalities=["text", "image"],
        supports_personality=True,
        service_tiers=[
            ModelServiceTier(
                id="priority",
                name="Fast",
                description="1.5x speed, increased usage",
            )
        ],
    )


def test_legacy_model_catalog_fixture_keeps_speed_tier_fallback() -> None:
    catalog = ModelListResult.model_validate(
        load_json("app_server", "model_list", "legacy_additional_speed_tiers.json")
    )

    assert catalog.data == [
        AppModel(
            id="legacy-fast-model",
            display_name="Legacy Fast Model",
            default_reasoning_effort="medium",
            supported_reasoning_efforts=["low", "medium"],
            additional_speed_tiers=["fast"],
        )
    ]


def test_thread_read_include_turns_alias() -> None:
    params = ThreadReadParams(thread_id="t1", include_turns=True)
    assert params.model_dump(by_alias=True, exclude_none=True) == {
        "threadId": "t1",
        "includeTurns": True,
    }


def test_thread_goal_set_aliases_and_goal_parsing() -> None:
    params = ThreadGoalSetParams(thread_id="t1", objective="ship", status="active", token_budget=10)
    assert params.model_dump(by_alias=True, exclude_none=True) == {
        "threadId": "t1",
        "objective": "ship",
        "status": "active",
        "tokenBudget": 10,
    }
    goal = ThreadGoal.model_validate(
        {
            "threadId": "t1",
            "objective": "ship",
            "status": "active",
            "tokensUsed": 3,
            "timeUsedSeconds": 4,
            "createdAt": 1,
            "updatedAt": 2,
        }
    )
    assert goal.thread_id == "t1"
    assert goal.tokens_used == 3


def test_thread_fork_rollback_and_compact_params() -> None:
    fork = ThreadForkParams(
        thread_id="t1",
        cwd="/w",
        sandbox="workspace-write",
        approval_policy="on-request",
        model="test-model",
        last_turn_id="turn-7",
        ephemeral=True,
    )
    assert fork.model_dump(by_alias=True, exclude_none=True) == {
        "threadId": "t1",
        "cwd": "/w",
        "sandbox": "workspace-write",
        "approvalPolicy": "on-request",
        "model": "test-model",
        "lastTurnId": "turn-7",
        "ephemeral": True,
    }
    assert ThreadRollbackParams(thread_id="t1", num_turns=2).model_dump(
        by_alias=True, exclude_none=True
    ) == {"threadId": "t1", "numTurns": 2}
    assert ThreadCompactStartParams(thread_id="t1").model_dump(
        by_alias=True, exclude_none=True
    ) == {"threadId": "t1"}


def test_initialize_result_parses_camelcase() -> None:
    result = InitializeResult.model_validate(
        {"userAgent": "ua", "codexHome": "/h", "platformFamily": "unix", "platformOs": "macos"}
    )
    assert result.codex_home == "/h"
    assert result.platform_family == "unix"
