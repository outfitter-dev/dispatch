"""Contract tests for persisted provider-capacity observations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import UsageInput
from outfitter.dispatch.registry.models import ProviderRuntimeSummary
from outfitter.dispatch.registry.store import Registry
from tests.fakes import make_ctx
from tests.fixtures.registry.builders import provider_capacity_observation


async def test_observation_freshness_is_deterministic_and_component_scoped(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 14, 12, 10, tzinfo=UTC)
    store = await Registry.open(tmp_path / "dispatch.db", now=lambda: now)
    try:
        observation = provider_capacity_observation(
            provider="claude",
            host_scope="node-a",
            config_scope="work",
            observed_at="2026-07-14T12:00:00+00:00",
        ).model_copy(
            update={
                "runtime": ProviderRuntimeSummary(
                    total_agents=2,
                    active_agents=1,
                    state_counts={"active": 1, "idle": 1},
                ),
                "account_observed_at": "2026-07-14T12:09:00+00:00",
                "runtime_observed_at": "2026-07-14T12:08:00+00:00",
                "capacity_observed_at": "2026-07-14T12:00:00+00:00",
                "usage_observed_at": "2026-07-14T12:07:00+00:00",
                "source": ["claude-auth-status", "claude-agents", "claude-statusline"],
            }
        )
        await store.upsert_provider_capacity_observation(observation)

        output = await handlers.usage(
            UsageInput(
                refresh=False,
                provider="claude",
                all_hosts=True,
                stale_after_seconds=300,
            ),
            make_ctx(store),
        )

        [view] = output.observations
        assert view.host == "node-a"
        assert view.config_scope == "work"
        assert view.freshness_seconds == 600
        assert view.account_freshness_seconds == 60
        assert view.runtime_freshness_seconds == 120
        assert view.capacity_freshness_seconds == 600
        assert view.usage_freshness_seconds == 180
        assert view.stale is True
        assert view.windows[0].freshness_seconds == 600
        assert view.windows[0].stale is True
        assert view.source == ["claude-auth-status", "claude-agents", "claude-statusline"]
    finally:
        await store.close()


async def test_observations_persist_latest_value_per_provider_host_and_config(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dispatch.db"
    store = await Registry.open(path)
    try:
        local = provider_capacity_observation(observed_at="2026-07-14T12:00:00+00:00")
        updated = local.model_copy(
            update={"plan": "team", "observed_at": "2026-07-14T12:05:00+00:00"}
        )
        remote = provider_capacity_observation(
            host_scope="node-b",
            config_scope="personal",
            observed_at="2026-07-14T12:03:00+00:00",
        )
        await store.upsert_provider_capacity_observation(local)
        await store.upsert_provider_capacity_observation(remote)
        await store.upsert_provider_capacity_observation(updated)
    finally:
        await store.close()

    reopened = await Registry.open(path)
    try:
        observations = await reopened.list_provider_capacity_observations(provider="codex")
        assert [(item.host_scope, item.config_scope) for item in observations] == [
            ("local", "default"),
            ("node-b", "personal"),
        ]
        assert observations[0].plan == "team"
        assert observations[0].observed_at == "2026-07-14T12:05:00+00:00"
        payload = observations[0].model_dump_json()
        assert "agent@example.com" not in payload
        assert "opaque-credit-1" not in payload
    finally:
        await reopened.close()


async def test_missing_provider_is_absence_with_refresh_hint(tmp_path: Path) -> None:
    store = await Registry.open(tmp_path / "dispatch.db")
    try:
        output = await handlers.usage(
            UsageInput(refresh=False, provider="missing-provider"), make_ctx(store)
        )

        assert output.observations == []
        assert output.hint == "run dispatch usage without --no-refresh to refresh local providers"
    finally:
        await store.close()
