"""Provider account/capacity normalization and notification refresh."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from outfitter.dispatch.client.errors import AppServerError
from outfitter.dispatch.client.events import AccountRateLimitsUpdated
from outfitter.dispatch.client.models import (
    AccountRateLimitsResult,
    AccountReadResult,
    AccountUsageResult,
    RateLimitResetCredit,
    RateLimitResetCreditsSummary,
    RateLimitSnapshot,
    RateLimitWindow,
    SpendControlLimitSnapshot,
)
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.capacity import observe_codex_rate_limits, refresh_codex_capacity
from outfitter.dispatch.core.models import UsageInput
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.models import ProviderCapacityWindow
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx
from tests.fixtures import load_json
from tests.fixtures.registry.builders import provider_capacity_observation


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    registry = await Registry.open()
    try:
        yield registry
    finally:
        await registry.close()


async def test_refresh_codex_capacity_redacts_and_replaces_current_observation(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.account_result = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    client.rate_limits_result = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "current.json")
    )
    client.usage_result = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "current.json")
    )
    ctx = make_ctx(store, client)

    first = await refresh_codex_capacity(ctx)
    second = await refresh_codex_capacity(ctx)

    assert first.state == "ready"
    assert first.account_label == "a***@example.com"
    assert first.account_fingerprint is not None
    assert first.account_fingerprint.startswith("sha256:")
    assert {window.window for window in first.windows} == {
        "primary",
        "secondary",
        "individual",
    }
    assert first.reset_credits_available == 2
    assert first.reset_credits[0].fingerprint.startswith("sha256:")
    assert first.usage_summary is not None
    assert first.usage_summary.lifetime_tokens == 123456
    assert first.account_observed_at == first.observed_at
    assert first.capacity_observed_at == first.observed_at
    assert first.usage_observed_at == first.observed_at
    assert second == await store.get_provider_capacity_observation("codex")
    [saved] = await store.list_provider_capacity_observations()
    payload = saved.model_dump_json()
    assert "agent@example.com" not in payload
    assert "opaque-credit-1" not in payload


async def test_refresh_codex_capacity_deduplicates_base_windows_absent_from_map(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.rate_limits_result = AccountRateLimitsResult(
        rate_limits=RateLimitSnapshot(
            limit_id="base",
            primary=RateLimitWindow(used_percent=25),
            individual_limit=SpendControlLimitSnapshot(
                limit="100",
                used="30",
                remaining_percent=70,
                resets_at=1784196000,
            ),
        ),
        rate_limits_by_limit_id={
            "other": RateLimitSnapshot(
                limit_id="other",
                primary=RateLimitWindow(used_percent=10),
            )
        },
    )
    ctx = make_ctx(store, client)

    observation = await refresh_codex_capacity(ctx)

    keys = [(window.limit_id, window.window) for window in observation.windows]
    assert keys == [("other", "primary"), ("base", "individual"), ("base", "primary")]
    assert len(keys) == len(set(keys))


async def test_refresh_codex_capacity_signed_out_skips_capacity_reads(store: Registry) -> None:
    client = FakeLaneClient()
    client.account_result = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_out.json")
    )

    observation = await refresh_codex_capacity(make_ctx(store, client))

    assert observation.state == "signed_out"
    assert [name for name, _ in client.calls] == ["account_read"]

    client.calls.clear()
    client.account_result = AccountReadResult(account=None, requires_openai_auth=False)
    disabled = await refresh_codex_capacity(make_ctx(store, client))
    assert disabled.state == "disabled"
    assert [name for name, _ in client.calls] == ["account_read"]


async def test_refresh_codex_capacity_marks_unsupported_and_partial_states(
    store: Registry,
) -> None:
    class UnsupportedClient(FakeLaneClient):
        async def account_read(self) -> AccountReadResult:
            raise AppServerError(-32601, "method not found")

    unsupported = await refresh_codex_capacity(make_ctx(store, UnsupportedClient()))
    assert unsupported.state == "unsupported"
    assert unsupported.error == "account/read unsupported"

    class UnavailableClient(FakeLaneClient):
        async def account_read(self) -> AccountReadResult:
            raise AppServerError(-32000, "provider unavailable with private detail")

    unavailable = await refresh_codex_capacity(make_ctx(store, UnavailableClient()))
    assert unavailable.state == "unavailable"
    assert unavailable.error == "account/read unavailable"
    assert "private detail" not in unavailable.model_dump_json()

    class PartialClient(FakeLaneClient):
        async def account_usage_read(self) -> AccountUsageResult:
            raise AppServerError(-32601, "method not found")

    partial = await refresh_codex_capacity(make_ctx(store, PartialClient()))
    assert partial.state == "partial"
    assert partial.error == "account/usage/read unsupported"
    assert partial.windows == []


async def test_refresh_codex_capacity_preserves_components_when_subprobes_fail(
    store: Registry,
) -> None:
    initial_client = FakeLaneClient()
    initial_client.account_result = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    initial_client.rate_limits_result = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "current.json")
    )
    initial_client.usage_result = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "current.json")
    )
    initial = await refresh_codex_capacity(make_ctx(store, initial_client))

    class LimitsUnavailableClient(FakeLaneClient):
        async def account_rate_limits_read(self) -> AccountRateLimitsResult:
            raise AppServerError(-32000, "temporarily unavailable")

    limits_unavailable = LimitsUnavailableClient()
    limits_unavailable.account_result = initial_client.account_result
    limits_unavailable.usage_result = initial_client.usage_result
    after_limits_failure = await refresh_codex_capacity(make_ctx(store, limits_unavailable))

    assert after_limits_failure.state == "partial"
    assert after_limits_failure.windows == initial.windows
    assert after_limits_failure.reset_credits == initial.reset_credits
    assert after_limits_failure.capacity_observed_at == initial.capacity_observed_at
    assert "account/rateLimits/read" in after_limits_failure.source

    class UsageUnavailableClient(FakeLaneClient):
        async def account_usage_read(self) -> AccountUsageResult:
            raise AppServerError(-32000, "temporarily unavailable")

    usage_unavailable = UsageUnavailableClient()
    usage_unavailable.account_result = initial_client.account_result
    usage_unavailable.rate_limits_result = initial_client.rate_limits_result
    after_usage_failure = await refresh_codex_capacity(make_ctx(store, usage_unavailable))

    assert after_usage_failure.state == "partial"
    assert after_usage_failure.usage_summary == after_limits_failure.usage_summary
    assert after_usage_failure.daily_usage == after_limits_failure.daily_usage
    assert after_usage_failure.usage_observed_at == after_limits_failure.usage_observed_at
    assert "account/usage/read" in after_usage_failure.source


async def test_refresh_codex_capacity_clears_reset_credits_when_success_omits_them(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.account_result = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    client.rate_limits_result = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "current.json")
    )
    client.usage_result = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "current.json")
    )
    initial = await refresh_codex_capacity(make_ctx(store, client))
    assert initial.reset_credits_available == 2
    assert initial.reset_credits

    client.rate_limits_result = client.rate_limits_result.model_copy(
        update={"rate_limit_reset_credits": None}
    )
    refreshed = await refresh_codex_capacity(make_ctx(store, client))

    assert refreshed.reset_credits_available is None
    assert refreshed.reset_credits == []


async def test_codex_plan_normalizes_whitespace_and_preserves_prior_value(
    store: Registry,
) -> None:
    existing = provider_capacity_observation().model_copy(update={"plan": "pro"})
    await store.upsert_provider_capacity_observation(existing)
    client = FakeLaneClient()
    signed_in = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    assert signed_in.account is not None
    client.account_result = signed_in.model_copy(
        update={"account": signed_in.account.model_copy(update={"plan_type": "   "})}
    )
    client.rate_limits_result = AccountRateLimitsResult(
        rate_limits=RateLimitSnapshot(plan_type="   ")
    )

    refreshed = await refresh_codex_capacity(make_ctx(store, client))
    pushed = await observe_codex_rate_limits(
        make_ctx(store),
        RateLimitSnapshot(plan_type="   ", primary=RateLimitWindow(used_percent=10)),
    )

    assert refreshed.plan == "pro"
    assert pushed.plan == "pro"


async def test_codex_window_and_credit_fields_are_bounded(store: Registry) -> None:
    client = FakeLaneClient()
    signed_in = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    client.account_result = signed_in
    long_name = "x" * 150
    client.rate_limits_result = AccountRateLimitsResult(
        rate_limits=RateLimitSnapshot(
            limit_id="codex",
            limit_name="   ",
            rate_limit_reached_type=long_name,
            primary=RateLimitWindow(used_percent=10),
        ),
        rate_limit_reset_credits=RateLimitResetCreditsSummary(
            available_count=2,
            credits=[
                RateLimitResetCredit(
                    id="opaque-credit-1",
                    reset_type=long_name,
                    status="  available  ",
                    granted_at=1783700000,
                    title="   ",
                ),
                RateLimitResetCredit(
                    id="opaque-credit-2",
                    reset_type="   ",
                    status="available",
                    granted_at=1783700001,
                    title="kept out",
                ),
            ],
        ),
    )

    refreshed = await refresh_codex_capacity(make_ctx(store, client))
    pushed = await observe_codex_rate_limits(
        make_ctx(store),
        RateLimitSnapshot(
            limit_id="codex",
            limit_name=long_name,
            rate_limit_reached_type="   ",
            primary=RateLimitWindow(used_percent=20),
        ),
    )

    assert refreshed.windows
    assert all(window.limit_name is None for window in refreshed.windows)
    assert all(
        window.reached_type is not None and len(window.reached_type) <= 120
        for window in refreshed.windows
    )
    assert len(refreshed.reset_credits) == 2
    assert refreshed.reset_credits[0].status == "available"
    assert len(refreshed.reset_credits[0].reset_type) <= 120
    assert refreshed.reset_credits[0].title is None
    assert refreshed.reset_credits[1].reset_type == "unknown"
    assert pushed.windows
    assert all(
        window.limit_name is not None and len(window.limit_name) <= 120 for window in pushed.windows
    )
    assert all(window.reached_type is None for window in pushed.windows)


async def test_codex_normalizes_bounded_window_and_reset_credit_text(store: Registry) -> None:
    client = FakeLaneClient()
    client.rate_limits_result = AccountRateLimitsResult(
        rate_limits=RateLimitSnapshot(
            limit_id="   ",
            limit_name="x" * 121,
            primary=RateLimitWindow(used_percent=10),
            rate_limit_reached_type="   ",
        ),
        rate_limit_reset_credits=RateLimitResetCreditsSummary(
            available_count=1,
            credits=[
                RateLimitResetCredit(
                    id="opaque-credit",
                    reset_type="x" * 121,
                    status="   ",
                    granted_at=1,
                )
            ],
        ),
    )

    observation = await refresh_codex_capacity(make_ctx(store, client))

    [window] = observation.windows
    assert window.limit_id == "default"
    assert window.limit_name == "x" * 119 + "…"
    assert window.reached_type is None
    [credit] = observation.reset_credits
    assert credit.reset_type == "x" * 119 + "…"
    assert credit.status == "unknown"


async def test_codex_normalizes_account_type(store: Registry) -> None:
    client = FakeLaneClient()
    signed_in = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    assert signed_in.account is not None
    client.account_result = signed_in.model_copy(
        update={"account": signed_in.account.model_copy(update={"type": "x" * 121})}
    )

    bounded = await refresh_codex_capacity(make_ctx(store, client))

    assert bounded.account_type == "x" * 119 + "…"

    client.account_result = signed_in.model_copy(
        update={"account": signed_in.account.model_copy(update={"type": "   "})}
    )
    fallback = await refresh_codex_capacity(make_ctx(store, client))

    assert fallback.account_type == "unknown"


async def test_refresh_codex_capacity_preserves_observation_when_account_probe_fails(
    store: Registry,
) -> None:
    initial_client = FakeLaneClient()
    initial_client.account_result = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    initial_client.rate_limits_result = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "current.json")
    )
    initial_client.usage_result = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "current.json")
    )
    initial = await refresh_codex_capacity(make_ctx(store, initial_client))

    class AccountUnavailableClient(FakeLaneClient):
        async def account_read(self) -> AccountReadResult:
            raise AppServerError(-32000, "temporarily unavailable")

    failed = await refresh_codex_capacity(make_ctx(store, AccountUnavailableClient()))

    assert failed.state == "unavailable"
    assert failed.account_fingerprint == initial.account_fingerprint
    assert failed.windows == initial.windows
    assert failed.usage_summary == initial.usage_summary
    assert failed.account_observed_at == initial.account_observed_at
    assert failed.capacity_observed_at == initial.capacity_observed_at
    assert failed.usage_observed_at == initial.usage_observed_at
    assert failed.error == "account/read unavailable"


async def test_rate_limit_notification_refreshes_without_polling(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    reactor = Reactor(ctx, TriggerRunner(ctx, now=lambda: store._now()))
    snapshot = RateLimitSnapshot(
        limit_id="codex",
        plan_type="pro",
        primary=RateLimitWindow(used_percent=60, window_duration_mins=300),
    )

    await reactor.handle_account_event(AccountRateLimitsUpdated(snapshot))

    saved = await store.get_provider_capacity_observation("codex")
    assert saved is not None
    assert saved.state == "partial"
    assert saved.windows[0].remaining_percent == 40
    assert saved.account_observed_at is None
    assert saved.capacity_observed_at == saved.observed_at
    assert saved.usage_observed_at is None
    assert saved.source == ["account/rateLimits/updated"]
    assert client.calls == []


async def test_rate_limit_notification_keeps_observation_window_bound(store: Registry) -> None:
    observed_at = "2026-07-14T12:00:00+00:00"
    existing = provider_capacity_observation(observed_at=observed_at).model_copy(
        update={
            "windows": [
                ProviderCapacityWindow(
                    limit_id=f"limit-{index}",
                    window="primary",
                    used_percent=index,
                    observed_at=observed_at,
                )
                for index in range(64)
            ]
        }
    )
    await store.upsert_provider_capacity_observation(existing)

    saved = await observe_codex_rate_limits(
        make_ctx(store),
        RateLimitSnapshot(
            limit_id="new-limit",
            primary=RateLimitWindow(used_percent=50),
        ),
    )

    assert len(saved.windows) == 64
    assert ("new-limit", "primary") in {
        (window.limit_id, window.window) for window in saved.windows
    }
    assert ("limit-0", "primary") not in {
        (window.limit_id, window.window) for window in saved.windows
    }


async def test_rate_limit_notification_preserves_component_freshness(store: Registry) -> None:
    client = FakeLaneClient()
    client.account_result = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    client.rate_limits_result = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "current.json")
    )
    client.usage_result = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "current.json")
    )
    ctx = make_ctx(store, client)
    before = await refresh_codex_capacity(ctx)
    before_secondary = next(
        window
        for window in before.windows
        if window.limit_id == "codex" and window.window == "secondary"
    )
    snapshot = RateLimitSnapshot(
        limit_id="codex",
        plan_type="team",
        primary=RateLimitWindow(used_percent=70),
    )

    await Reactor(ctx, TriggerRunner(ctx, now=lambda: store._now())).handle_account_event(
        AccountRateLimitsUpdated(snapshot)
    )

    after = await store.get_provider_capacity_observation("codex")
    assert after is not None
    assert after.state == "ready"
    assert after.plan == "team"
    assert after.account_observed_at == before.account_observed_at
    assert after.usage_observed_at == before.usage_observed_at
    assert after.capacity_observed_at == after.observed_at
    assert any(
        window.limit_id == "codex" and window.window == "secondary" for window in after.windows
    )
    after_primary = next(
        window
        for window in after.windows
        if window.limit_id == "codex" and window.window == "primary"
    )
    after_secondary = next(
        window
        for window in after.windows
        if window.limit_id == "codex" and window.window == "secondary"
    )
    assert after_primary.observed_at == after.observed_at
    assert after_secondary.observed_at == before_secondary.observed_at


async def test_idless_rate_limit_notification_reuses_unambiguous_named_limit(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.account_result = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    client.rate_limits_result = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "current.json")
    )
    client.usage_result = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "current.json")
    )
    ctx = make_ctx(store, client)
    before = await refresh_codex_capacity(ctx)
    before_secondary = next(
        window
        for window in before.windows
        if window.limit_id == "codex" and window.window == "secondary"
    )

    await Reactor(ctx, TriggerRunner(ctx, now=lambda: store._now())).handle_account_event(
        AccountRateLimitsUpdated(
            RateLimitSnapshot(
                limit_name="Codex",
                primary=RateLimitWindow(used_percent=70),
            )
        )
    )

    after = await store.get_provider_capacity_observation("codex")
    assert after is not None
    primary = [window for window in after.windows if window.window == "primary"]
    assert {(window.limit_id, window.used_percent) for window in primary} == {
        ("codex", 70),
        ("review", 10),
    }
    assert all(window.limit_id != "default" for window in after.windows)
    after_secondary = next(
        window
        for window in after.windows
        if window.limit_id == "codex" and window.window == "secondary"
    )
    assert after_secondary.observed_at == before_secondary.observed_at


async def test_whitespace_id_rate_limit_notification_uses_normalized_matching(
    store: Registry,
) -> None:
    observed_at = "2026-07-14T12:00:00+00:00"
    existing = provider_capacity_observation(observed_at=observed_at).model_copy(
        update={
            "windows": [
                ProviderCapacityWindow(
                    limit_id="codex",
                    limit_name="Codex",
                    window="primary",
                    used_percent=10,
                    observed_at=observed_at,
                ),
                ProviderCapacityWindow(
                    limit_id="review",
                    limit_name="Review",
                    window="primary",
                    used_percent=5,
                    observed_at=observed_at,
                ),
            ]
        }
    )
    await store.upsert_provider_capacity_observation(existing)

    matched = await observe_codex_rate_limits(
        make_ctx(store),
        RateLimitSnapshot(
            limit_id="   ",
            limit_name="Codex",
            primary=RateLimitWindow(used_percent=20),
        ),
    )

    assert {(window.limit_id, window.used_percent) for window in matched.windows} == {
        ("codex", 20),
        ("review", 5),
    }

    unmatched = await observe_codex_rate_limits(
        make_ctx(store),
        RateLimitSnapshot(
            limit_id="   ",
            limit_name="Other",
            primary=RateLimitWindow(used_percent=30),
        ),
    )

    assert {(window.limit_id, window.used_percent) for window in unmatched.windows} == {
        ("codex", 20),
        ("review", 5),
        ("default", 30),
    }


async def test_idless_rate_limit_notification_reuses_sole_existing_limit(
    store: Registry,
) -> None:
    before = provider_capacity_observation()
    await store.upsert_provider_capacity_observation(before)
    ctx = make_ctx(store, FakeLaneClient())

    await Reactor(ctx, TriggerRunner(ctx, now=lambda: store._now())).handle_account_event(
        AccountRateLimitsUpdated(RateLimitSnapshot(primary=RateLimitWindow(used_percent=70)))
    )

    after = await store.get_provider_capacity_observation("codex")
    assert after is not None
    assert [(window.limit_id, window.window, window.used_percent) for window in after.windows] == [
        ("codex", "primary", 70)
    ]
    assert after.account_observed_at == before.account_observed_at
    assert after.usage_observed_at == before.usage_observed_at
    assert after.capacity_observed_at == after.observed_at


async def test_usage_reads_cache_filters_future_providers_and_marks_stale(
    store: Registry,
) -> None:
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await store.upsert_provider_capacity_observation(
        provider_capacity_observation(provider="claude", host_scope="mini", observed_at=old)
    )
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    output = await handlers.usage(
        UsageInput(
            refresh=False,
            provider="claude",
            all_hosts=True,
            stale_after_seconds=300,
            include_daily=False,
        ),
        ctx,
    )

    assert output.refreshed_providers == []
    assert len(output.observations) == 1
    observation = output.observations[0]
    assert observation.provider == "claude"
    assert observation.host == "mini"
    assert observation.stale is True
    assert observation.windows[0].stale is True
    assert observation.daily_usage == []
    assert client.calls == []


def test_usage_rejects_conflicting_host_filters() -> None:
    with pytest.raises(ValueError, match="all_hosts"):
        UsageInput(host="mini", all_hosts=True)


async def test_usage_refreshes_local_codex_and_can_include_daily(store: Registry) -> None:
    client = FakeLaneClient()
    client.account_result = AccountReadResult.model_validate(
        load_json("app_server", "account_read", "signed_in.json")
    )
    client.rate_limits_result = AccountRateLimitsResult.model_validate(
        load_json("app_server", "account_rate_limits", "current.json")
    )
    client.usage_result = AccountUsageResult.model_validate(
        load_json("app_server", "account_usage", "current.json")
    )

    output = await handlers.usage(
        UsageInput(provider="codex", include_daily=True), make_ctx(store, client)
    )

    assert output.refreshed_providers == ["codex"]
    assert len(output.observations) == 1
    observation = output.observations[0]
    assert observation.state == "ready"
    assert observation.stale is False
    assert observation.daily_usage[-1].tokens == 3400
    payload = output.model_dump_json()
    assert "agent@example.com" not in payload
    assert "opaque-credit-1" not in payload
