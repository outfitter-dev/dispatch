"""Provider account/capacity normalization and notification refresh."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from outfitter.dispatch.client.errors import AppServerError
from outfitter.dispatch.client.events import AccountRateLimitsUpdated
from outfitter.dispatch.client.models import (
    AccountRateLimitsResult,
    AccountReadResult,
    AccountUsageResult,
    RateLimitSnapshot,
    RateLimitWindow,
)
from outfitter.dispatch.core.capacity import refresh_codex_capacity
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx
from tests.fixtures import load_json


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
