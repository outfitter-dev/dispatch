"""Normalize redacted provider account and capacity observations."""

from __future__ import annotations

import asyncio
import hashlib

from pydantic import ValidationError as PydanticValidationError

from outfitter.dispatch.client.errors import AppServerError, ClientError
from outfitter.dispatch.client.models import (
    AccountRateLimitsResult,
    AccountUsageResult,
    RateLimitSnapshot,
)
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.registry.models import (
    ProviderCapacityObservation,
    ProviderCapacityState,
    ProviderCapacityWindow,
    ProviderDailyUsage,
    ProviderResetCredit,
    ProviderUsageSummary,
)


def _fingerprint(value: str) -> str:
    digest = hashlib.sha256(value.strip().lower().encode()).hexdigest()
    return f"sha256:{digest[:24]}"


def _masked_email(value: str) -> str:
    local, separator, domain = value.strip().partition("@")
    if not separator or not local or not domain:
        return "redacted"
    return f"{local[0]}***@{domain.lower()}"


def _bounded(value: str | None, limit: int = 120) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    if not collapsed:
        return None
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def _windows(
    snapshot: RateLimitSnapshot, *, fallback_id: str, observed_at: str
) -> list[ProviderCapacityWindow]:
    limit_id = _bounded(snapshot.limit_id) or _bounded(fallback_id) or "default"
    limit_name = _bounded(snapshot.limit_name)
    reached_type = _bounded(snapshot.rate_limit_reached_type)
    result: list[ProviderCapacityWindow] = []
    for name, window in (("primary", snapshot.primary), ("secondary", snapshot.secondary)):
        if window is None:
            continue
        used = max(0, min(100, window.used_percent))
        result.append(
            ProviderCapacityWindow(
                limit_id=limit_id,
                limit_name=limit_name,
                window=name,
                used_percent=used,
                remaining_percent=100 - used,
                duration_minutes=window.window_duration_mins,
                resets_at=window.resets_at,
                reached_type=reached_type,
                observed_at=observed_at,
            )
        )
    if snapshot.individual_limit is not None:
        remaining = max(0, min(100, snapshot.individual_limit.remaining_percent))
        result.append(
            ProviderCapacityWindow(
                limit_id=limit_id,
                limit_name=limit_name,
                window="individual",
                remaining_percent=remaining,
                used_percent=100 - remaining,
                resets_at=snapshot.individual_limit.resets_at,
                reached_type=reached_type,
                observed_at=observed_at,
            )
        )
    return result


def _all_windows(
    limits: AccountRateLimitsResult, *, observed_at: str
) -> list[ProviderCapacityWindow]:
    snapshots = limits.rate_limits_by_limit_id
    if snapshots:
        result = [
            window
            for limit_id, snapshot in sorted(snapshots.items())
            for window in _windows(snapshot, fallback_id=limit_id, observed_at=observed_at)
        ]
        base = limits.rate_limits
        base_id = base.limit_id or "default"
        if base.individual_limit is not None:
            result.extend(
                window
                for window in _windows(base, fallback_id=base_id, observed_at=observed_at)
                if window.window == "individual"
            )
        if base_id not in snapshots:
            result.extend(_windows(base, fallback_id=base_id, observed_at=observed_at))
        return list({(window.limit_id, window.window): window for window in result}.values())[:64]
    return _windows(limits.rate_limits, fallback_id="default", observed_at=observed_at)[:64]


def _push_limit_id(
    snapshot: RateLimitSnapshot, existing: ProviderCapacityObservation | None
) -> str:
    if snapshot.limit_id is not None:
        return snapshot.limit_id
    if existing is None:
        return "default"

    limit_name = _bounded(snapshot.limit_name)
    if limit_name is not None:
        named_ids = {
            window.limit_id for window in existing.windows if window.limit_name == limit_name
        }
        if len(named_ids) == 1:
            return named_ids.pop()

    existing_ids = {window.limit_id for window in existing.windows}
    return existing_ids.pop() if len(existing_ids) == 1 else "default"


def _usage_summary(usage: AccountUsageResult) -> ProviderUsageSummary:
    summary = usage.summary
    return ProviderUsageSummary(
        lifetime_tokens=summary.lifetime_tokens,
        current_streak_days=summary.current_streak_days,
        longest_streak_days=summary.longest_streak_days,
        peak_daily_tokens=summary.peak_daily_tokens,
        longest_running_turn_seconds=summary.longest_running_turn_sec,
    )


def _error_state(exc: BaseException) -> ProviderCapacityState:
    if isinstance(exc, AppServerError) and exc.code == -32601:
        return "unsupported"
    return "unavailable"


async def _save_account_failure(
    ctx: Ctx,
    *,
    existing: ProviderCapacityObservation | None,
    state: ProviderCapacityState,
    observed_at: str,
    error: str,
) -> ProviderCapacityObservation:
    source = list(existing.source) if existing is not None else []
    if "account/read" not in source:
        source.append("account/read")
    if existing is not None:
        return await ctx.registry.upsert_provider_capacity_observation(
            existing.model_copy(
                update={
                    "state": state,
                    "source": source,
                    "observed_at": observed_at,
                    "confidence": 0.0,
                    "error": error,
                }
            )
        )
    return await ctx.registry.upsert_provider_capacity_observation(
        ProviderCapacityObservation(
            provider="codex",
            state=state,
            source=source,
            observed_at=observed_at,
            confidence=0.0,
            error=error,
        )
    )


async def refresh_codex_capacity(ctx: Ctx) -> ProviderCapacityObservation:
    """Refresh one local Codex observation without retaining raw auth payloads."""

    observed_at = ctx.registry.now_iso()
    existing = await ctx.registry.get_provider_capacity_observation("codex")
    try:
        account = await ctx.client.account_read()
    except ClientError as exc:
        state = _error_state(exc)
        return await _save_account_failure(
            ctx,
            existing=existing,
            state=state,
            observed_at=observed_at,
            error=f"account/read {state}",
        )
    except PydanticValidationError:
        return await _save_account_failure(
            ctx,
            existing=existing,
            state="unavailable",
            observed_at=observed_at,
            error="account/read invalid response",
        )

    if account.account is None:
        state = "signed_out" if account.requires_openai_auth else "disabled"
        return await ctx.registry.upsert_provider_capacity_observation(
            ProviderCapacityObservation(
                provider="codex",
                state=state,
                requires_auth=account.requires_openai_auth,
                source=["account/read"],
                observed_at=observed_at,
                account_observed_at=observed_at,
                confidence=1.0,
            )
        )

    limits_result, usage_result = await asyncio.gather(
        ctx.client.account_rate_limits_read(),
        ctx.client.account_usage_read(),
        return_exceptions=True,
    )
    limits = limits_result if isinstance(limits_result, AccountRateLimitsResult) else None
    usage = usage_result if isinstance(usage_result, AccountUsageResult) else None
    sources = list(existing.source) if existing is not None else []
    if "account/read" not in sources:
        sources.append("account/read")
    errors: list[str] = []
    if limits is not None:
        if "account/rateLimits/read" not in sources:
            sources.append("account/rateLimits/read")
    else:
        assert isinstance(limits_result, BaseException)
        errors.append(f"account/rateLimits/read {_error_state(limits_result)}")
    if usage is not None:
        if "account/usage/read" not in sources:
            sources.append("account/usage/read")
    else:
        assert isinstance(usage_result, BaseException)
        errors.append(f"account/usage/read {_error_state(usage_result)}")

    email = account.account.email
    snapshots = (
        limits.rate_limits_by_limit_id.values() if limits and limits.rate_limits_by_limit_id else []
    )
    snapshot_plans = (_bounded(snapshot.plan_type) for snapshot in snapshots)
    plan = (
        _bounded(account.account.plan_type)
        or next((value for value in snapshot_plans if value is not None), None)
        or (existing.plan if existing is not None else None)
    )
    credits = limits.rate_limit_reset_credits if limits is not None else None
    credit_rows = credits.credits if credits is not None and credits.credits is not None else []
    base_snapshot = limits.rate_limits if limits is not None else None
    daily = usage.daily_usage_buckets if usage is not None and usage.daily_usage_buckets else []
    windows = (
        _all_windows(limits, observed_at=observed_at)
        if limits is not None
        else existing.windows
        if existing is not None
        else []
    )
    reset_credits_available = (
        credits.available_count
        if credits is not None
        else None
        if limits is not None
        else existing.reset_credits_available
        if existing is not None
        else None
    )
    reset_credits = (
        [
            ProviderResetCredit(
                fingerprint=_fingerprint(credit.id),
                reset_type=_bounded(credit.reset_type) or "unknown",
                status=_bounded(credit.status) or "unknown",
                granted_at=credit.granted_at,
                expires_at=credit.expires_at,
                title=_bounded(credit.title),
            )
            for credit in credit_rows
        ]
        if credits is not None
        else []
        if limits is not None
        else existing.reset_credits
        if existing is not None
        else []
    )
    usage_summary = (
        _usage_summary(usage)
        if usage is not None
        else existing.usage_summary
        if existing is not None
        else None
    )
    daily_usage = (
        [
            ProviderDailyUsage(start_date=bucket.start_date, tokens=bucket.tokens)
            for bucket in sorted(daily, key=lambda item: item.start_date)[-90:]
        ]
        if usage is not None
        else existing.daily_usage
        if existing is not None
        else []
    )
    observation = ProviderCapacityObservation(
        provider="codex",
        state="ready" if not errors else "partial",
        account_type=account.account.type,
        account_fingerprint=_fingerprint(email) if email else None,
        account_label=_masked_email(email) if email else None,
        plan=plan,
        requires_auth=account.requires_openai_auth,
        windows=windows,
        reset_credits_available=reset_credits_available,
        reset_credits=reset_credits,
        usage_summary=usage_summary,
        daily_usage=daily_usage,
        has_credits=(
            base_snapshot.credits.has_credits
            if base_snapshot is not None and base_snapshot.credits is not None
            else existing.has_credits
            if existing is not None and limits is None
            else None
        ),
        unlimited_credits=(
            base_snapshot.credits.unlimited
            if base_snapshot is not None and base_snapshot.credits is not None
            else existing.unlimited_credits
            if existing is not None and limits is None
            else None
        ),
        source=sources,
        observed_at=observed_at,
        account_observed_at=observed_at,
        capacity_observed_at=(
            observed_at
            if limits is not None
            else existing.capacity_observed_at
            if existing is not None
            else None
        ),
        usage_observed_at=(
            observed_at
            if usage is not None
            else existing.usage_observed_at
            if existing is not None
            else None
        ),
        confidence=1.0 if not errors else 0.7,
        error="; ".join(errors) if errors else None,
    )
    return await ctx.registry.upsert_provider_capacity_observation(observation)


async def observe_codex_rate_limits(
    ctx: Ctx, snapshot: RateLimitSnapshot
) -> ProviderCapacityObservation:
    """Merge a provider push notification into the current local observation."""

    existing = await ctx.registry.get_provider_capacity_observation("codex")
    observed_at = ctx.registry.now_iso()
    replacement = _windows(
        snapshot,
        fallback_id=_push_limit_id(snapshot, existing),
        observed_at=observed_at,
    )
    replaced = {(window.limit_id, window.window) for window in replacement}
    retained = (
        [window for window in existing.windows if (window.limit_id, window.window) not in replaced]
        if existing is not None
        else []
    )
    source = list(existing.source) if existing is not None else []
    if "account/rateLimits/updated" not in source:
        source.append("account/rateLimits/updated")
    prior_is_usable = existing is not None and existing.state in {"ready", "partial"}
    observation = (
        existing.model_copy(
            update={
                "state": existing.state if prior_is_usable else "partial",
                "windows": (retained + replacement)[-64:],
                "source": source,
                "observed_at": observed_at,
                "capacity_observed_at": observed_at,
                "plan": _bounded(snapshot.plan_type) or existing.plan,
                "confidence": max(existing.confidence, 0.8),
                "error": existing.error
                if prior_is_usable
                else "account and historical usage not observed",
            }
        )
        if existing is not None
        else ProviderCapacityObservation(
            provider="codex",
            state="partial",
            plan=_bounded(snapshot.plan_type),
            windows=replacement,
            source=source,
            observed_at=observed_at,
            capacity_observed_at=observed_at,
            confidence=0.8,
            error="account and historical usage not observed",
        )
    )
    return await ctx.registry.upsert_provider_capacity_observation(observation)
