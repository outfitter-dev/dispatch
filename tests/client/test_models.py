"""Unit tests for the wire models: alias round-tripping + the verified gotchas."""

from __future__ import annotations

from outfitter.dispatch.client.models import (
    AppModel,
    ConfigInfo,
    InitializeResult,
    ModelListResult,
    ModelServiceTier,
    SandboxPolicy,
    TextInput,
    ThreadCompactStartParams,
    ThreadForkParams,
    ThreadGoal,
    ThreadGoalSetParams,
    ThreadInfo,
    ThreadListParams,
    ThreadListResult,
    ThreadReadParams,
    ThreadResumeParams,
    ThreadRollbackParams,
    ThreadStartParams,
    TurnStartParams,
    TurnSteerParams,
)


def test_thread_start_sandbox_is_string_enum() -> None:
    params = ThreadStartParams(cwd="/work", sandbox="workspace-write", ephemeral=True)
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped["sandbox"] == "workspace-write"  # STRING, not an object
    assert dumped["approvalPolicy"] == "never"
    assert dumped["ephemeral"] is True


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


def test_turn_start_includes_effort_when_set() -> None:
    params = TurnStartParams(thread_id="t1", input=[TextInput(text="hi")], cwd="/w", effort="low")
    assert params.model_dump(by_alias=True, exclude_none=True)["effort"] == "low"


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


def test_thread_list_params_include_current_native_filters() -> None:
    params = ThreadListParams(
        limit=25,
        archived=False,
        cwd=["/repo", "/other"],
        search_term="schema drift",
        sort_direction="desc",
        sort_key="updated_at",
        source_kinds=["cli", "appServer"],
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
        "useStateDbOnly": True,
    }


def test_thread_resume_can_request_low_hydration_subscription() -> None:
    params = ThreadResumeParams(thread_id="t1", exclude_turns=True)
    assert params.model_dump(by_alias=True, exclude_none=True) == {
        "threadId": "t1",
        "excludeTurns": True,
    }


def test_thread_info_keeps_sync_metadata_fields() -> None:
    thread = ThreadInfo.model_validate(
        {
            "id": "t1",
            "sessionId": "t1",
            "parentThreadId": "parent",
            "path": "/tmp/rollout.jsonl",
            "modelProvider": "openai",
            "threadSource": "user",
            "updatedAt": 123,
        }
    )

    assert thread.session_id == "t1"
    assert thread.parent_thread_id == "parent"
    assert thread.path == "/tmp/rollout.jsonl"
    assert thread.model_provider == "openai"
    assert thread.thread_source == "user"
    assert thread.updated_at == 123


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
    config = ConfigInfo.model_validate(
        {
            "model": "gpt-5.5",
            "modelProvider": "openai",
            "serviceTier": "priority",
            "modelReasoningEffort": "xhigh",
        }
    )
    catalog = ModelListResult.model_validate(
        {
            "data": [
                {
                    "id": "gpt-5.5",
                    "displayName": "GPT-5.5",
                    "defaultReasoningEffort": "xhigh",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "faster"},
                        {"reasoningEffort": "xhigh", "description": "deeper"},
                    ],
                    "serviceTiers": [
                        {
                            "id": "priority",
                            "name": "Fast",
                            "description": "1.5x speed, increased usage",
                        }
                    ],
                    "additionalSpeedTiers": ["fast"],
                }
            ]
        }
    )

    assert config.model_provider == "openai"
    assert catalog.data == [
        AppModel(
            id="gpt-5.5",
            display_name="GPT-5.5",
            default_reasoning_effort="xhigh",
            supported_reasoning_efforts=["low", "xhigh"],
            service_tiers=[
                ModelServiceTier(
                    id="priority",
                    name="Fast",
                    description="1.5x speed, increased usage",
                )
            ],
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
        ephemeral=True,
    )
    assert fork.model_dump(by_alias=True, exclude_none=True) == {
        "threadId": "t1",
        "cwd": "/w",
        "sandbox": "workspace-write",
        "approvalPolicy": "on-request",
        "model": "test-model",
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
