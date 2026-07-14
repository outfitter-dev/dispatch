"""Contract tests for persisted provider-capacity observations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    finally:
        await reopened.close()


async def test_invalid_copied_observation_is_rejected_before_replacing_row(tmp_path: Path) -> None:
    store = await Registry.open(tmp_path / "dispatch.db")
    try:
        valid = provider_capacity_observation()
        await store.upsert_provider_capacity_observation(valid)
        invalid = valid.model_copy(update={"source": [f"source-{index}" for index in range(17)]})

        with pytest.raises(ValidationError):
            await store.upsert_provider_capacity_observation(invalid)

        assert await store.get_provider_capacity_observation("codex") == valid
    finally:
        await store.close()


async def test_legacy_oversized_windows_are_bounded_when_read(tmp_path: Path) -> None:
    store = await Registry.open(tmp_path / "dispatch.db")
    try:
        valid = provider_capacity_observation()
        await store.upsert_provider_capacity_observation(valid)
        payload = valid.model_dump(mode="json")
        [window] = payload["windows"]
        payload["windows"] = [{**window, "limit_id": f"legacy-{index}"} for index in range(65)]
        await store._conn.execute(
            "UPDATE provider_capacity_observations SET payload = ?",
            (json.dumps(payload),),
        )
        await store._conn.commit()

        loaded = await store.get_provider_capacity_observation("codex")

        assert loaded is not None
        assert len(loaded.windows) == 64
        assert loaded.windows[0].limit_id == "legacy-1"
        assert loaded.windows[-1].limit_id == "legacy-64"
    finally:
        await store.close()


def test_observation_provenance_is_bounded() -> None:
    observation = provider_capacity_observation()
    data = observation.model_dump()

    with pytest.raises(ValidationError, match="List should have at most 16 items"):
        type(observation).model_validate(
            {**data, "source": [f"source-{index}" for index in range(17)]}
        )

    with pytest.raises(ValidationError, match="String should have at most 120 characters"):
        type(observation).model_validate({**data, "source": ["x" * 121]})


def test_new_observation_rejects_oversized_window_collection() -> None:
    observation = provider_capacity_observation()
    data = observation.model_dump()
    [window] = data["windows"]

    with pytest.raises(ValidationError, match="List should have at most 64 items"):
        type(observation).model_validate(
            {
                **data,
                "windows": [{**window, "limit_id": f"new-{index}"} for index in range(65)],
            }
        )


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("account_label", "agent@example.com"),
        ("account_fingerprint", "raw-account-id"),
        ("organization_fingerprint", "opaque-org-id"),
        ("organization_label", "x" * 121),
        ("error", "x" * 501),
        ("observed_at", "not-a-timestamp"),
    ],
)
def test_observation_rejects_unsafe_persisted_values(field: str, unsafe_value: str) -> None:
    observation = provider_capacity_observation()

    with pytest.raises(ValidationError):
        type(observation).model_validate({**observation.model_dump(), field: unsafe_value})


@pytest.mark.parametrize(
    "credit_update",
    [
        {"fingerprint": "opaque-credit-1"},
        {"title": "x" * 121},
        {"reset_type": "x" * 121},
        {"status": "x" * 121},
    ],
)
def test_observation_rejects_unsafe_reset_credit_values(credit_update: dict[str, str]) -> None:
    observation = provider_capacity_observation()
    data = observation.model_dump()
    [credit] = data["reset_credits"]

    with pytest.raises(ValidationError):
        type(observation).model_validate({**data, "reset_credits": [{**credit, **credit_update}]})


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
