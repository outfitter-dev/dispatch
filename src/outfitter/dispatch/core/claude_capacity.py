"""Normalize Claude account and runtime observations from read-only CLI commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.core.claude_statusline import (
    read_claude_statusline_snapshot,
    statusline_capacity_windows,
)
from outfitter.dispatch.registry.models import (
    ProviderCapacityObservation,
    ProviderRuntimeSummary,
)


@dataclass(frozen=True)
class ClaudeCommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


ClaudeCommandRunner = Callable[[tuple[str, ...]], Awaitable[ClaudeCommandResult]]
_ACTIVE_AGENT_STATES = {"active", "blocked", "running", "working"}
_AGENT_STATES = _ACTIVE_AGENT_STATES | {"done", "failed", "idle", "stopped", "unknown"}
_MAX_AGENTS = 1000
_MAX_COMMAND_BYTES = 1024 * 1024
_VERSION_PATTERN = re.compile(r"\b\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b")


class ClaudeCommandOutputError(Exception):
    """Raised when a Claude CLI stream exceeds its bounded read limit."""


async def _read_bounded(stream: asyncio.StreamReader) -> bytes:
    result = bytearray()
    while chunk := await stream.read(64 * 1024):
        result.extend(chunk)
        if len(result) > _MAX_COMMAND_BYTES:
            raise ClaudeCommandOutputError
    return bytes(result)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await process.wait()


async def run_claude_command(args: tuple[str, ...]) -> ClaudeCommandResult:
    """Run one bounded read-only Claude CLI query."""

    process = await asyncio.create_subprocess_exec(
        "claude",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=64 * 1024,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    tasks = [
        asyncio.create_task(_read_bounded(process.stdout)),
        asyncio.create_task(_read_bounded(process.stderr)),
        asyncio.create_task(process.wait()),
    ]
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.shield(_terminate(process))
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    stdout = cast(bytes, results[0])
    stderr = cast(bytes, results[1])
    return ClaudeCommandResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


def _fingerprint(value: str) -> str:
    digest = hashlib.sha256(value.strip().lower().encode()).hexdigest()
    return f"sha256:{digest[:24]}"


def _masked_email(value: str) -> str:
    local, separator, domain = value.strip().partition("@")
    if not separator or not local or not domain:
        return "redacted"
    return f"{local[0]}***@{domain.lower()}"


def _bounded(value: object, limit: int = 120) -> str | None:
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def _agent_state(agent: dict[str, object]) -> str:
    value = agent.get("state")
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in _AGENT_STATES else "unknown"


async def _save_auth_failure(
    ctx: Ctx,
    *,
    existing: ProviderCapacityObservation | None,
    observed_at: str,
    error: str,
) -> ProviderCapacityObservation:
    source = list(existing.source) if existing is not None else []
    if "claude auth status --json" not in source:
        source.append("claude auth status --json")
    if existing is not None:
        return await ctx.registry.upsert_provider_capacity_observation(
            existing.model_copy(
                update={
                    "state": "unavailable",
                    "source": source,
                    "observed_at": observed_at,
                    "confidence": 0.0,
                    "error": error,
                }
            )
        )
    return await ctx.registry.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="unavailable",
            source=source,
            observed_at=observed_at,
            confidence=0.0,
            error=error,
        )
    )


async def refresh_claude_capacity(
    ctx: Ctx, *, run_command: ClaudeCommandRunner | None = None
) -> ProviderCapacityObservation:
    """Refresh one local Claude observation without retaining raw CLI payloads."""

    observed_at = ctx.registry.now_iso()
    existing = await ctx.registry.get_provider_capacity_observation("claude")
    runner = run_command or run_claude_command
    try:
        auth_result = await runner(("auth", "status", "--json"))
    except FileNotFoundError:
        return await _save_auth_failure(
            ctx, existing=existing, observed_at=observed_at, error="claude CLI unavailable"
        )
    except TimeoutError:
        return await _save_auth_failure(
            ctx, existing=existing, observed_at=observed_at, error="claude CLI timed out"
        )
    except ClaudeCommandOutputError:
        return await _save_auth_failure(
            ctx,
            existing=existing,
            observed_at=observed_at,
            error="claude CLI output too large",
        )
    if auth_result.returncode != 0:
        return await _save_auth_failure(
            ctx,
            existing=existing,
            observed_at=observed_at,
            error="claude auth status failed",
        )
    try:
        raw_auth = json.loads(auth_result.stdout)
    except (json.JSONDecodeError, TypeError):
        return await _save_auth_failure(
            ctx,
            existing=existing,
            observed_at=observed_at,
            error="claude auth status returned invalid JSON",
        )
    if not isinstance(raw_auth, dict) or not isinstance(raw_auth.get("loggedIn"), bool):
        return await _save_auth_failure(
            ctx,
            existing=existing,
            observed_at=observed_at,
            error="claude auth status returned incompatible JSON",
        )
    auth = cast(dict[str, object], raw_auth)
    if not bool(auth.get("loggedIn")):
        source = list(existing.source) if existing is not None else []
        if "claude auth status --json" not in source:
            source.append("claude auth status --json")
        if existing is not None:
            return await ctx.registry.upsert_provider_capacity_observation(
                existing.model_copy(
                    update={
                        "state": "signed_out",
                        "account_type": "claude.ai",
                        "account_fingerprint": None,
                        "account_label": None,
                        "auth_method": _bounded(auth.get("authMethod")),
                        "api_provider": _bounded(auth.get("apiProvider")),
                        "organization_fingerprint": None,
                        "organization_label": None,
                        "cli_version": None,
                        "plan": None,
                        "requires_auth": True,
                        "runtime": None,
                        "source": source,
                        "observed_at": observed_at,
                        "account_observed_at": observed_at,
                        "runtime_observed_at": None,
                        "confidence": 1.0,
                        "error": None,
                    }
                )
            )
        return await ctx.registry.upsert_provider_capacity_observation(
            ProviderCapacityObservation(
                provider="claude",
                state="signed_out",
                account_type="claude.ai",
                auth_method=_bounded(auth.get("authMethod")),
                api_provider=_bounded(auth.get("apiProvider")),
                requires_auth=True,
                source=source,
                observed_at=observed_at,
                account_observed_at=observed_at,
                confidence=1.0,
            )
        )
    errors: list[str] = []
    cli_version = existing.cli_version if existing is not None else None
    try:
        version_result = await runner(("--version",))
    except (FileNotFoundError, TimeoutError, ClaudeCommandOutputError):
        errors.append("claude version unavailable")
    else:
        match = _VERSION_PATTERN.search(version_result.stdout)
        if version_result.returncode == 0 and match is not None:
            cli_version = match.group(0)
        else:
            errors.append("claude version unavailable")
    runtime = existing.runtime if existing is not None else None
    runtime_observed_at = existing.runtime_observed_at if existing is not None else None
    runtime_error: str | None = None
    try:
        agents_result = await runner(("agents", "--json"))
    except TimeoutError:
        agents_result = None
        runtime_error = "claude agents timed out"
    except ClaudeCommandOutputError:
        agents_result = None
        runtime_error = "claude agents output too large"
    except FileNotFoundError:
        agents_result = None
        runtime_error = "claude CLI unavailable"
    if agents_result is None:
        pass
    elif agents_result.returncode != 0:
        runtime_error = "claude agents failed"
    else:
        try:
            raw_agents = json.loads(agents_result.stdout)
            if (
                not isinstance(raw_agents, list)
                or not all(isinstance(agent, dict) for agent in raw_agents)
                or len(raw_agents) > _MAX_AGENTS
            ):
                raise TypeError
            agents = cast(list[dict[str, object]], raw_agents)
            states = Counter(_agent_state(agent) for agent in agents)
            runtime = ProviderRuntimeSummary(
                total_agents=len(agents),
                active_agents=sum(states[state] for state in _ACTIVE_AGENT_STATES),
                state_counts=dict(sorted(states.items())),
            )
            runtime_observed_at = observed_at
        except (json.JSONDecodeError, TypeError):
            runtime_error = "claude agents returned invalid JSON"
    if runtime_error is not None:
        errors.append(runtime_error)
    windows = existing.windows if existing is not None else []
    capacity_observed_at = existing.capacity_observed_at if existing is not None else None
    statusline = await asyncio.to_thread(read_claude_statusline_snapshot)
    statusline_source: str | None = None
    if statusline is not None:
        statusline_source = "claude statusline snapshot"
        captured_windows = statusline_capacity_windows(statusline)
        if captured_windows:
            existing_capacity_at = (
                datetime.fromisoformat(capacity_observed_at)
                if capacity_observed_at is not None
                else None
            )
            snapshot_at = datetime.fromisoformat(statusline.observed_at)
            if existing_capacity_at is None or snapshot_at > existing_capacity_at:
                windows = captured_windows
                capacity_observed_at = statusline.observed_at
                if cli_version is None:
                    cli_version = statusline.claude_code_version
        else:
            errors.append("claude statusline rate limits unavailable")
    email = auth.get("email")
    org_id = auth.get("orgId")
    organization = auth.get("orgName")
    source = list(existing.source) if existing is not None else []
    for name in ("claude auth status --json", "claude --version", "claude agents --json"):
        if name not in source:
            source.append(name)
    if statusline_source is not None and statusline_source not in source:
        source.append(statusline_source)
    observation = ProviderCapacityObservation(
        provider="claude",
        state="partial" if errors else "ready",
        account_type="claude.ai",
        account_fingerprint=_fingerprint(email) if isinstance(email, str) else None,
        account_label=_masked_email(email) if isinstance(email, str) else None,
        auth_method=_bounded(auth.get("authMethod")),
        api_provider=_bounded(auth.get("apiProvider")),
        organization_fingerprint=_fingerprint(org_id) if isinstance(org_id, str) else None,
        organization_label=_bounded(organization),
        cli_version=cli_version,
        plan=_bounded(auth.get("subscriptionType")),
        requires_auth=not bool(auth.get("loggedIn")),
        runtime=runtime,
        windows=windows,
        reset_credits_available=existing.reset_credits_available if existing is not None else None,
        reset_credits=existing.reset_credits if existing is not None else [],
        usage_summary=existing.usage_summary if existing is not None else None,
        daily_usage=existing.daily_usage if existing is not None else [],
        has_credits=existing.has_credits if existing is not None else None,
        unlimited_credits=existing.unlimited_credits if existing is not None else None,
        source=source,
        observed_at=observed_at,
        account_observed_at=observed_at,
        runtime_observed_at=runtime_observed_at,
        capacity_observed_at=capacity_observed_at,
        usage_observed_at=existing.usage_observed_at if existing is not None else None,
        confidence=0.7 if errors else 1.0,
        error="; ".join(errors) if errors else None,
    )
    return await ctx.registry.upsert_provider_capacity_observation(observation)
