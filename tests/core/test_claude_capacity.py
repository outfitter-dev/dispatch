"""Claude account/runtime observations through supported read-only CLI surfaces."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.claude_capacity import (
    ClaudeCommandOutputError,
    ClaudeCommandResult,
    _merge_windows,
    refresh_claude_capacity,
    run_claude_command,
)
from outfitter.dispatch.core.claude_statusline import capture_claude_statusline
from outfitter.dispatch.core.models import UsageInput
from outfitter.dispatch.registry.models import (
    ProviderCapacityObservation,
    ProviderCapacityWindow,
    ProviderRuntimeSummary,
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


def _command_response(
    responses: dict[tuple[str, ...], ClaudeCommandResult], args: tuple[str, ...]
) -> ClaudeCommandResult:
    if args == ("--version",):
        return ClaudeCommandResult(0, "2.1.206 (Claude Code)\n")
    return responses[args]


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
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert calls == [
        ("auth", "status", "--json"),
        ("--version",),
        ("agents", "--json"),
    ]
    assert observation.provider == "claude"
    assert observation.state == "ready"
    assert observation.account_label == "a***@example.com"
    assert observation.account_fingerprint is not None
    assert observation.auth_method == "claude.ai"
    assert observation.api_provider == "firstParty"
    assert observation.organization_label == "Outfitter"
    assert observation.organization_fingerprint is not None
    assert observation.plan == "max"
    assert observation.cli_version == "2.1.206"
    assert observation.runtime is not None
    assert observation.runtime.total_agents == 2
    assert observation.runtime.active_agents == 1
    assert observation.runtime.state_counts == {"active": 1, "idle": 1}
    assert observation.account_observed_at == observation.observed_at
    assert observation.runtime_observed_at == observation.observed_at
    assert observation.capacity_observed_at is None


async def test_claude_email_masking_cannot_exceed_persisted_label_bound(
    store: Registry,
) -> None:
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "email": f"a@{'x' * 252}",
                }
            ),
        ),
        ("agents", "--json"): ClaudeCommandResult(returncode=0, stdout="[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.account_label == "redacted"
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


async def test_refresh_claude_capacity_normalizes_whitespace_only_optional_fields(
    store: Registry,
) -> None:
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "   ",
                    "apiProvider": "   ",
                    "email": "agent@example.com",
                    "orgName": "   ",
                    "subscriptionType": "   ",
                }
            ),
        ),
        ("agents", "--json"): ClaudeCommandResult(returncode=0, stdout="[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store), run_command=run)

    assert observation.state == "ready"
    assert observation.auth_method is None
    assert observation.api_provider is None
    assert observation.organization_label is None
    assert observation.plan is None


def test_merge_windows_keeps_incoming_window_within_observation_bound() -> None:
    observed_at = "2026-07-14T12:00:00+00:00"
    existing = [
        ProviderCapacityWindow(
            limit_id=f"existing-{index}",
            window="primary",
            used_percent=index,
            observed_at=observed_at,
        )
        for index in range(64)
    ]
    captured = [
        ProviderCapacityWindow(
            limit_id="incoming",
            window="five_hour",
            used_percent=50,
            observed_at="2026-07-14T12:01:00+00:00",
        )
    ]

    merged = _merge_windows(existing, captured)

    assert len(merged) == 64
    assert ("incoming", "five_hour") in {(window.limit_id, window.window) for window in merged}


async def test_refresh_claude_capacity_signed_out_skips_runtime_probe(store: Registry) -> None:
    await store.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="ready",
            cli_version="2.1.100",
            runtime=ProviderRuntimeSummary(
                total_agents=1,
                active_agents=1,
                state_counts={"active": 1},
            ),
            windows=[
                ProviderCapacityWindow(
                    limit_id="claude.ai",
                    window="seven_day",
                    used_percent=40,
                    observed_at="2026-07-14T12:00:00+00:00",
                )
            ],
            source=["claude statusline"],
            observed_at="2026-07-14T12:00:00+00:00",
            runtime_observed_at="2026-07-14T12:00:00+00:00",
            capacity_observed_at="2026-07-14T12:00:00+00:00",
            confidence=0.8,
        )
    )
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
    assert observation.cli_version is None
    assert observation.account_observed_at == observation.observed_at
    assert observation.windows[0].window == "seven_day"
    assert observation.capacity_observed_at == "2026-07-14T12:00:00+00:00"


async def test_refresh_claude_capacity_marks_missing_cli_unavailable(store: Registry) -> None:
    await store.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="partial",
            windows=[
                ProviderCapacityWindow(
                    limit_id="claude.ai",
                    window="five_hour",
                    used_percent=25,
                    observed_at="2026-07-14T12:00:00+00:00",
                )
            ],
            source=["claude statusline"],
            observed_at="2026-07-14T12:00:00+00:00",
            capacity_observed_at="2026-07-14T12:00:00+00:00",
            confidence=0.8,
        )
    )

    async def run(_args: tuple[str, ...]) -> ClaudeCommandResult:
        raise FileNotFoundError("/private/path/claude")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "unavailable"
    assert observation.error == "claude CLI unavailable"
    assert observation.windows[0].window == "five_hour"
    assert observation.capacity_observed_at == "2026-07-14T12:00:00+00:00"
    assert observation.source == ["claude statusline", "claude auth status --json"]
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
if [ "$1" = "--version" ]; then
  printf '%s\n' '2.1.206 (Claude Code)'
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


@pytest.mark.parametrize("failed_provider", ["codex", "claude"])
async def test_usage_isolates_unexpected_provider_refresh_failures(
    store: Registry, monkeypatch: pytest.MonkeyPatch, failed_provider: str
) -> None:
    async def refresh(provider: str) -> ProviderCapacityObservation:
        if provider == failed_provider:
            raise RuntimeError("private provider failure")
        return await store.upsert_provider_capacity_observation(
            ProviderCapacityObservation(
                provider=provider,
                state="ready",
                observed_at=store.now_iso(),
                confidence=1.0,
            )
        )

    async def codex(_ctx: object) -> ProviderCapacityObservation:
        return await refresh("codex")

    async def claude(_ctx: object) -> ProviderCapacityObservation:
        return await refresh("claude")

    monkeypatch.setattr(handlers, "refresh_codex_capacity", codex)
    monkeypatch.setattr(handlers, "refresh_claude_capacity", claude)

    output = await handlers.usage(UsageInput(), make_ctx(store, FakeLaneClient()))

    successful = "claude" if failed_provider == "codex" else "codex"
    assert output.refreshed_providers == [successful]
    assert [item.provider for item in output.observations] == [successful]
    assert "private provider failure" not in output.model_dump_json()


async def test_claude_command_runner_rejects_oversized_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = tmp_path / "claude"
    claude.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(b'x' * (1024 * 1024 + 1))\n"
    )
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(ClaudeCommandOutputError):
        await run_claude_command(("auth", "status", "--json"))


async def test_claude_command_runner_reaps_child_when_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_path = tmp_path / "claude.pid"
    claude = tmp_path / "claude"
    claude.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, time\n"
        "pathlib.Path(os.environ['CLAUDE_TEST_PID']).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    claude.chmod(0o755)
    monkeypatch.setenv("CLAUDE_TEST_PID", str(pid_path))
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(run_claude_command(("auth", "status", "--json")))
    for _ in range(100):
        if pid_path.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_path.exists()
    pid = int(pid_path.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


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
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert [(window.window, window.used_percent) for window in observation.windows] == [
        ("five_hour", 25)
    ]
    assert observation.capacity_observed_at == "2026-07-14T12:00:00+00:00"
    assert observation.source == [
        "claude statusline",
        "claude auth status --json",
        "claude --version",
        "claude agents --json",
    ]


async def test_refresh_claude_capacity_keeps_account_when_agents_output_is_invalid(
    store: Registry,
) -> None:
    await store.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="ready",
            account_label="o***@example.com",
            runtime=ProviderRuntimeSummary(
                total_agents=2,
                active_agents=1,
                state_counts={"active": 1, "idle": 1},
            ),
            observed_at="2026-07-14T12:00:00+00:00",
            account_observed_at="2026-07-14T12:00:00+00:00",
            runtime_observed_at="2026-07-14T12:00:00+00:00",
            confidence=1.0,
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
        ("agents", "--json"): ClaudeCommandResult(0, "private malformed roster"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "partial"
    assert observation.account_label == "a***@example.com"
    assert observation.runtime is not None
    assert observation.runtime.total_agents == 2
    assert observation.runtime.state_counts == {"active": 1, "idle": 1}
    assert observation.runtime_observed_at == "2026-07-14T12:00:00+00:00"
    assert observation.error == "claude agents returned invalid JSON"
    assert "private malformed roster" not in observation.model_dump_json()


async def test_refresh_claude_capacity_sanitizes_auth_command_failure(store: Registry) -> None:
    await store.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="ready",
            account_label="o***@example.com",
            account_fingerprint="sha256:0123456789abcdef",
            observed_at="2026-07-14T12:00:00+00:00",
            account_observed_at="2026-07-14T12:00:00+00:00",
            confidence=1.0,
        )
    )

    async def run(_args: tuple[str, ...]) -> ClaudeCommandResult:
        return ClaudeCommandResult(2, "", "private auth failure")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "unavailable"
    assert observation.account_label == "o***@example.com"
    assert observation.account_observed_at == "2026-07-14T12:00:00+00:00"
    assert observation.observed_at != observation.account_observed_at
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
        return _command_response(responses, args)

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
        if args == ("--version",):
            return ClaudeCommandResult(0, "2.1.206 (Claude Code)\n")
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
                    {"state": {"cwd": "/private/leak"}},
                ]
            ),
        ),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.runtime is not None
    assert observation.runtime.total_agents == 4
    assert observation.runtime.active_agents == 1
    assert observation.runtime.state_counts == {"blocked": 1, "done": 1, "unknown": 2}
    assert "/private/leak" not in observation.model_dump_json()


async def test_refresh_claude_capacity_merges_fresh_statusline_windows(
    store: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    captured = capture_claude_statusline(
        json.dumps(
            {
                "session_id": "private-session",
                "version": "2.1.206",
                "model": {"display_name": "Opus"},
                "rate_limits": {
                    "five_hour": {"used_percentage": 23.5, "resets_at": 1_738_425_600},
                    "seven_day": {"used_percentage": 41.2, "resets_at": 1_738_857_600},
                },
            }
        ).encode(),
        observed_at=datetime.now(UTC).isoformat(),
    )
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
        ),
        ("agents", "--json"): ClaudeCommandResult(0, "[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert [(window.window, window.used_percent) for window in observation.windows] == [
        ("five_hour", 23.5),
        ("seven_day", 41.2),
    ]
    assert observation.capacity_observed_at == captured.observed_at
    assert "claude statusline snapshot" in observation.source
    assert "private-session" not in observation.model_dump_json()


async def test_refresh_claude_capacity_preserves_capacity_when_statusline_has_no_limits(
    store: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    await store.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="ready",
            windows=[
                ProviderCapacityWindow(
                    limit_id="claude.ai",
                    window="five_hour",
                    used_percent=30,
                    observed_at="2026-07-14T12:00:00+00:00",
                )
            ],
            observed_at="2026-07-14T12:00:00+00:00",
            capacity_observed_at="2026-07-14T12:00:00+00:00",
            confidence=1.0,
        )
    )
    capture_claude_statusline(
        json.dumps({"session_id": "private-session", "version": "2.1.206"}).encode(),
        observed_at=datetime.now(UTC).isoformat(),
    )
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
        ),
        ("agents", "--json"): ClaudeCommandResult(0, "[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.windows[0].used_percent == 30
    assert observation.capacity_observed_at == "2026-07-14T12:00:00+00:00"
    assert observation.error == "claude statusline rate limits unavailable"


async def test_refresh_claude_capacity_imports_cached_windows_when_current_limits_unavailable(
    store: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    capture_claude_statusline(
        json.dumps(
            {"rate_limits": {"five_hour": {"used_percentage": 25, "resets_at": 2}}}
        ).encode(),
        observed_at="2026-07-14T18:00:00+00:00",
    )
    capture_claude_statusline(
        json.dumps({"session_id": "new-session", "version": "2.1.207"}).encode(),
        observed_at="2026-07-14T19:00:00+00:00",
    )
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
        ),
        ("agents", "--json"): ClaudeCommandResult(0, "[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "partial"
    assert observation.error == "claude statusline rate limits unavailable"
    assert len(observation.windows) == 1
    assert observation.windows[0].used_percent == 25
    assert observation.windows[0].observed_at == "2026-07-14T18:00:00+00:00"
    assert observation.capacity_observed_at == "2026-07-14T18:00:00+00:00"


@pytest.mark.parametrize(
    ("existing_window", "captured_window"),
    [("five_hour", "seven_day"), ("seven_day", "five_hour")],
)
async def test_refresh_claude_capacity_merges_partial_statusline_by_window(
    store: Registry,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    existing_window: str,
    captured_window: str,
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    await store.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="ready",
            windows=[
                ProviderCapacityWindow(
                    limit_id="claude.ai",
                    window=existing_window,
                    used_percent=10,
                    observed_at="2026-07-14T12:00:00+00:00",
                )
            ],
            observed_at="2026-07-14T12:00:00+00:00",
            capacity_observed_at="2026-07-14T12:00:00+00:00",
            confidence=1.0,
        )
    )
    capture_claude_statusline(
        json.dumps(
            {"rate_limits": {captured_window: {"used_percentage": 20, "resets_at": 2}}}
        ).encode(),
        observed_at="2026-07-14T19:00:00+00:00",
    )
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
        ),
        ("agents", "--json"): ClaudeCommandResult(0, "[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    windows = {window.window: window for window in observation.windows}
    assert set(windows) == {"five_hour", "seven_day"}
    assert windows[existing_window].used_percent == 10
    assert windows[existing_window].observed_at == "2026-07-14T12:00:00+00:00"
    assert windows[captured_window].used_percent == 20
    assert windows[captured_window].observed_at == "2026-07-14T19:00:00+00:00"


async def test_refresh_claude_capacity_does_not_replace_newer_capacity_snapshot(
    store: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    await store.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="ready",
            windows=[
                ProviderCapacityWindow(
                    limit_id="claude.ai",
                    window="seven_day",
                    used_percent=30,
                    observed_at="2026-07-14T12:00:00+00:00",
                )
            ],
            observed_at="2026-07-14T12:00:00+00:00",
            capacity_observed_at="2026-07-14T12:00:00+00:00",
            confidence=1.0,
        )
    )
    capture_claude_statusline(
        json.dumps(
            {"rate_limits": {"seven_day": {"used_percentage": 90, "resets_at": 1}}}
        ).encode(),
        observed_at="2020-01-01T00:00:00+00:00",
    )
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
        ),
        ("agents", "--json"): ClaudeCommandResult(0, "[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.windows[0].used_percent == 30
    assert observation.capacity_observed_at == "2026-07-14T12:00:00+00:00"
    assert observation.error is None


async def test_usage_marks_old_statusline_capacity_stale(
    store: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    capture_claude_statusline(
        json.dumps(
            {"rate_limits": {"seven_day": {"used_percentage": 90, "resets_at": 1}}}
        ).encode(),
        observed_at="2020-01-01T00:00:00+00:00",
    )
    responses = {
        ("auth", "status", "--json"): ClaudeCommandResult(
            0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
        ),
        ("agents", "--json"): ClaudeCommandResult(0, "[]"),
    }

    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        return _command_response(responses, args)

    await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)
    output = await handlers.usage(
        UsageInput(refresh=False, provider="claude", stale_after_seconds=300),
        make_ctx(store, FakeLaneClient()),
    )

    assert output.observations[0].stale is True
    assert output.observations[0].capacity_freshness_seconds is not None
    assert output.observations[0].windows[0].stale is True


async def test_refresh_claude_capacity_marks_unparseable_version_partial(store: Registry) -> None:
    async def run(args: tuple[str, ...]) -> ClaudeCommandResult:
        if args == ("auth", "status", "--json"):
            return ClaudeCommandResult(0, '{"loggedIn":true}')
        if args == ("--version",):
            return ClaudeCommandResult(0, "private unparseable version")
        return ClaudeCommandResult(0, "[]")

    observation = await refresh_claude_capacity(make_ctx(store, FakeLaneClient()), run_command=run)

    assert observation.state == "partial"
    assert observation.cli_version is None
    assert observation.error == "claude version unavailable"
    assert "private unparseable" not in observation.model_dump_json()
