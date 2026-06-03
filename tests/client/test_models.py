"""Unit tests for the wire models: alias round-tripping + the verified gotchas."""

from __future__ import annotations

from outfitter.dispatch.client.models import (
    InitializeResult,
    SandboxPolicy,
    TextInput,
    ThreadListResult,
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


def test_initialize_result_parses_camelcase() -> None:
    result = InitializeResult.model_validate(
        {"userAgent": "ua", "codexHome": "/h", "platformFamily": "unix", "platformOs": "macos"}
    )
    assert result.codex_home == "/h"
    assert result.platform_family == "unix"
