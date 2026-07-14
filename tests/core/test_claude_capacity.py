"""Claude account/runtime observations through supported read-only CLI surfaces."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.claude_capacity import (
    ClaudeCommandResult,
    refresh_claude_capacity,
)
from outfitter.dispatch.core.models import UsageInput
from outfitter.dispatch.registry.models import (
    ProviderCapacityObservation,
    ProviderCapacityWindow,
)
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    registry = await Registry.open()
    try:
        yield registry
    finally:
        await registry.close()


async def test_refresh_claude_capacity_normalizes_account_and_runtime_without_roster_data(
    store: Registry,
) -> None:
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "email": "agent@example.com",
                    "orgId": "org-sensitive-id",
                    "orgName": "Outfitter",
                    "subscriptionType": "max",
                }
            ),
        ),
        ("agents", "--json"): ClaudeCommandResult(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "cwd": "/private/workspace",
                        "id": "agent-secret-1",
                        "name": "secret-agent-name",
                        "sessionId": "session-secret-1",
                        "state": "active",
                    },
                    {
                        "cwd": "/private/other",
                        "id": "agent-secret-2",
                        "sessionId": "session-secret-2",
                        "state": "idle",
                    },
                ]
            ),
        ),
    }
    calls: list[tuple[str, ...]] = []

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        calls.append(args)
        return responses[args]

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert calls == [("auth", "status", "--json"), ("agents", "--json")]
    assert observation.provider == "claude"
    assert observation.state == "ready"
    assert observation.account_label == "a***@example.com"
    assert observation.account_fingerprint is not None
    assert observation.auth_method == "claude.ai"
    assert observation.api_provider == "firstParty"
    assert observation.organization_label == "Outfitter"
    assert observation.organization_fingerprint is not None
    assert observation.plan == "max"
    assert observation.runtime is not None
    assert observation.runtime.total_agents == 2
    assert observation.runtime.active_agents == 1
    assert observation.runtime.state_counts == {"active": 1, "idle": 1}
    assert observation.account_observed_at == observation.observed_at
    assert observation.runtime_observed_at == observation.observed_at
    assert observation.capacity_observed_at is None
    payload = observation.model_dump_json()
    for secret in (
        "agent@example.com",
        "org-sensitive-id",
        "/private/workspace",
        "agent-secret-1",
        "secret-agent-name",
        "session-secret-1",
    ):
        assert secret not in payload


async def test_refresh_claude_capacity_signed_out_skips_runtime_probe(store: Registry) -> None:
    calls: list[tuple[str, ...]] = []

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        calls.append(args)
        return ClaudeCommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "loggedIn": False,
                    "authMethod": "none",
                    "apiProvider": "firstParty",
                }
            ),
        )

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert calls == [("auth", "status", "--json")]
    assert observation.state == "signed_out"
    assert observation.requires_auth is True
    assert observation.runtime is None
    assert observation.runtime_observed_at is None
    assert observation.account_observed_at == observation.observed_at


async def test_refresh_claude_capacity_marks_missing_cli_unavailable(store: Registry) -> None:
    async def run(_args: tuple[str, ...]) -> ClaudeCommandResult:
        raise FileNotFoundError("/private/path/claude")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "unavailable"
    assert observation.source == ["claude auth status --json"]
    assert observation.error == "claude CLI unavailable"
    assert "/private/path" not in observation.model_dump_json()


async def test_refresh_claude_capacity_marks_malformed_auth_output_unavailable(
    store: Registry,
) -> None:
    async def run(_args: tuple[str, ...]) -> ClaudeCommandResult:
        return ClaudeCommandResult(returncode=0, stdout="not-json private-detail")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "unavailable"
    assert observation.error == "claude auth status returned invalid JSON"
    assert "private-detail" not in observation.model_dump_json()


async def test_refresh_claude_capacity_marks_incompatible_auth_shape_unavailable(
    store: Registry,
) -> None:
    async def run(_args: tuple[str, ...]) -> ClaudeCommandResult:
        return ClaudeCommandResult(returncode=0, stdout="[]")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "unavailable"
    assert observation.error == "claude auth status returned incompatible JSON"


async def test_usage_refreshes_codex_and_claude_through_read_only_cli(
    store: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = tmp_path / "claude"
    claude.write_text(
        """#!/bin/sh
if [ "$1" = "auth" ]; then
  printf '%s' '{"loggedIn":true,"authMethod":"claude.ai",'
  printf '%s\n' '"apiProvider":"firstParty","email":"agent@example.com","subscriptionType":"max"}'
  exit 0
fi
if [ "$1" = "agents" ]; then
  printf '%s\n' '[{"cwd":"/private/workspace","id":"secret","sessionId":"secret","state":"active"}]'
  exit 0
fi
exit 2
"""
    )
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    output = await handlers.usage(UsageInput(), make_ctx(store, FakeLaneClient()))

    assert output.refreshed_providers == ["codex", "claude"]
    assert {item.provider for item in output.observations} == {"codex", "claude"}
    claude_view = next(item for item in output.observations if item.provider == "claude")
    assert claude_view.runtime is not None
    assert claude_view.runtime.active_agents == 1
    assert claude_view.runtime_freshness_seconds == 0
    assert "agent@example.com" not in output.model_dump_json()
    assert "/private/workspace" not in output.model_dump_json()


async def test_refresh_claude_capacity_preserves_existing_capacity_windows(
    store: Registry,
) -> None:
    await store.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="partial",
            windows=[
                ProviderCapacityWindow(
                    limit_id="claude.ai",
                    window="five_hour",
                    used_percent=25,
                    remaining_percent=75,
                    observed_at="2026-07-14T12:00:00+00:00",
                )
            ],
            source=["claude statusline"],
            observed_at="2026-07-14T12:00:00+00:00",
            capacity_observed_at="2026-07-14T12:00:00+00:00",
            confidence=0.8,
        )
    )
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0,
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "email": "agent@example.com",
                }
            ),
        ),
        ("agents", "--json"): ClaudeCommandResult(0, "[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return responses[args]

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert [(window.window, window.used_percent) for window in observation.windows] == [
        ("five_hour", 25)
    ]
    assert observation.capacity_observed_at == "2026-07-14T12:00:00+00:00"
    assert observation.source == [
        "claude statusline",
        "claude auth status --json",
        "claude agents --json",
    ]


async def test_refresh_claude_capacity_keeps_account_when_agents_output_is_invalid(
    store: Registry,
) -> None:
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0,
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "email": "agent@example.com",
                }
            ),
        ),
        ("agents", "--json"): ClaudeCommandResult(0, "private malformed roster"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return responses[args]

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "partial"
    assert observation.account_label == "a***@example.com"
    assert observation.runtime is None
    assert observation.runtime_observed_at is None
    assert observation.error == "claude agents returned invalid JSON"
    assert "private malformed roster" not in observation.model_dump_json()


async def test_refresh_claude_capacity_sanitizes_auth_command_failure(store: Registry) -> None:
    async def run(_args: tuple[str, ...]) -> ClaudeCommandResult:
        return ClaudeCommandResult(2, "", "private auth failure")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "unavailable"
    assert observation.error == "claude auth status failed"
    assert "private auth failure" not in observation.model_dump_json()


async def test_refresh_claude_capacity_marks_probe_timeout_unavailable(store: Registry) -> None:
    async def run(_args: tuple[str, ...]) -> ClaudeCommandResult:
        raise TimeoutError("private timeout detail")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "unavailable"
    assert observation.error == "claude CLI timed out"
    assert "private timeout" not in observation.model_dump_json()


async def test_refresh_claude_capacity_keeps_account_when_agents_command_fails(
    store: Registry,
) -> None:
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0,
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "email": "agent@example.com",
                }
            ),
        ),
        ("agents", "--json"): ClaudeCommandResult(2, "", "private roster failure"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return responses[args]

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "partial"
    assert observation.account_label == "a***@example.com"
    assert observation.runtime is None
    assert observation.error == "claude agents failed"
    assert "private roster failure" not in observation.model_dump_json()


async def test_refresh_claude_capacity_keeps_account_when_agents_probe_times_out(
    store: Registry,
) -> None:
    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        if args == ("auth", "status", "--json"):
            return ClaudeCommandResult(
                0,
                json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "email": "agent@example.com",
                    }
                ),
            )
        raise TimeoutError("private roster timeout")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "partial"
    assert observation.account_label == "a***@example.com"
    assert observation.runtime is None
    assert observation.error == "claude agents timed out"
    assert "private roster timeout" not in observation.model_dump_json()


async def test_refresh_claude_capacity_counts_blocked_agents_as_active_runtime(
    store: Registry,
) -> None:
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0,
            json.dumps({"loggedIn": True, "authMethod": "claude.ai"}),
        ),
        ("agents", "--json"): ClaudeCommandResult(
            0,
            json.dumps(
                [
                    {"state": "blocked"},
                    {"state": "done"},
                    {"state": "unknown"},
                ]
            ),
        ),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return responses[args]

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.runtime is not None
    assert observation.runtime.active_agents == 1
    assert observation.runtime.state_counts == {"blocked": 1, "done": 1, "unknown": 1}
