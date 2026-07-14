"""Normalize Claude account and runtime observations from read-only CLI commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from outfitter.dispatch.contracts.context import Ctx
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


async def run_claude_command(args: tuple[str, ...]) -> ClaudeCommandResult:
    """Run one bounded read-only Claude CLI query."""

    process = await asyncio.create_subprocess_exec(
        "claude",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1024 * 1024,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise
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


async def _save_auth_failure(
    ctx: Ctx, *, observed_at: str, error: str
) -> ProviderCapacityObservation:
    return await ctx.registry.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="claude",
            state="unavailable",
            source=["claude auth status --json"],
            observed_at=observed_at,
            account_observed_at=observed_at,
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
            ctx, observed_at=observed_at, error="claude CLI unavailable"
        )
    except TimeoutError:
        return await _save_auth_failure(ctx, observed_at=observed_at, error="claude CLI timed out")
    if auth_result.returncode != 0:
        return await _save_auth_failure(
            ctx, observed_at=observed_at, error="claude auth status failed"
        )
    try:
        raw_auth = json.loads(auth_result.stdout)
    except (json.JSONDecodeError, TypeError):
        return await _save_auth_failure(
            ctx,
            observed_at=observed_at,
            error="claude auth status returned invalid JSON",
        )
    if not isinstance(raw_auth, dict) or not isinstance(raw_auth.get("loggedIn"), bool):
        return await _save_auth_failure(
            ctx,
            observed_at=observed_at,
            error="claude auth status returned incompatible JSON",
        )
    auth = cast(dict[str, object], raw_auth)
    if not bool(auth.get("loggedIn")):
        return await ctx.registry.upsert_provider_capacity_observation(
            ProviderCapacityObservation(
                provider="claude",
                state="signed_out",
                account_type="claude.ai",
                auth_method=str(auth["authMethod"]) if auth.get("authMethod") is not None else None,
                api_provider=str(auth["apiProvider"])
                if auth.get("apiProvider") is not None
                else None,
                requires_auth=True,
                source=["claude auth status --json"],
                observed_at=observed_at,
                account_observed_at=observed_at,
                confidence=1.0,
            )
        )
    runtime: ProviderRuntimeSummary | None = None
    runtime_error: str | None = None
    try:
        agents_result = await runner(("agents", "--json"))
    except TimeoutError:
        agents_result = None
        runtime_error = "claude agents timed out"
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
            if not isinstance(raw_agents, list) or not all(
                isinstance(agent, dict) for agent in raw_agents
            ):
                raise TypeError
            agents = cast(list[dict[str, object]], raw_agents)
            states = Counter(str(agent.get("state", "unknown")).lower() for agent in agents)
            runtime = ProviderRuntimeSummary(
                total_agents=len(agents),
                active_agents=sum(states[state] for state in _ACTIVE_AGENT_STATES),
                state_counts=dict(sorted(states.items())),
            )
        except (json.JSONDecodeError, TypeError):
            runtime_error = "claude agents returned invalid JSON"
    email = auth.get("email")
    org_id = auth.get("orgId")
    organization = auth.get("orgName")
    source = list(existing.source) if existing is not None else []
    for name in ("claude auth status --json", "claude agents --json"):
        if name not in source:
            source.append(name)
    observation = ProviderCapacityObservation(
        provider="claude",
        state="partial" if runtime_error else "ready",
        account_type="claude.ai",
        account_fingerprint=_fingerprint(email) if isinstance(email, str) else None,
        account_label=_masked_email(email) if isinstance(email, str) else None,
        auth_method=str(auth["authMethod"]) if auth.get("authMethod") is not None else None,
        api_provider=str(auth["apiProvider"]) if auth.get("apiProvider") is not None else None,
        organization_fingerprint=_fingerprint(org_id) if isinstance(org_id, str) else None,
        organization_label=str(organization) if organization is not None else None,
        plan=str(auth["subscriptionType"]) if auth.get("subscriptionType") is not None else None,
        requires_auth=not bool(auth.get("loggedIn")),
        runtime=runtime,
        windows=existing.windows if existing is not None else [],
        reset_credits_available=existing.reset_credits_available if existing is not None else None,
        reset_credits=existing.reset_credits if existing is not None else [],
        usage_summary=existing.usage_summary if existing is not None else None,
        daily_usage=existing.daily_usage if existing is not None else [],
        has_credits=existing.has_credits if existing is not None else None,
        unlimited_credits=existing.unlimited_credits if existing is not None else None,
        source=source,
        observed_at=observed_at,
        account_observed_at=observed_at,
        runtime_observed_at=observed_at if runtime is not None else None,
        capacity_observed_at=existing.capacity_observed_at if existing is not None else None,
        usage_observed_at=existing.usage_observed_at if existing is not None else None,
        confidence=0.7 if runtime_error else 1.0,
        error=runtime_error,
    )
    return await ctx.registry.upsert_provider_capacity_observation(observation)
