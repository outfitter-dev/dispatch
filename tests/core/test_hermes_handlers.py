"""End-to-end handler coverage for the narrow Hermes provider slice."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio

from outfitter.dispatch.client.hermes import (
    HermesSessionCreated,
    HermesSessionCreationResult,
    HermesSessionCreationUnknown,
)
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    CapabilityUnavailableError,
    DeliveryConflictError,
    ValidationError,
)
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.hermes import (
    HermesAttentionObservation,
    HermesSessionActivityObservation,
    HermesTranscriptObservation,
    apply_hermes_activity_observation,
    apply_hermes_attention_observation,
    apply_hermes_delivery_observation,
    apply_hermes_transcript_observation,
    quarantine_stale_hermes_lanes,
)
from outfitter.dispatch.core.models import (
    NewInput,
    RosterInput,
    SendInput,
    ShowInput,
    TranscriptInput,
)
from outfitter.dispatch.core.providers import (
    PreparedProviderRequest,
    ProviderAction,
    ProviderAvailability,
    ProviderBindingFacts,
    ProviderDurability,
    ProviderRouter,
    ProviderSubmissionAccepted,
    ProviderSubmissionRejected,
    ProviderSubmissionResult,
    ProviderSubmissionUnknown,
)
from outfitter.dispatch.registry.models import LaneRuntimeState
from outfitter.dispatch.registry.observations import ProviderCorrelation, ProviderObservation
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    registry = await Registry.open()
    try:
        yield registry
    finally:
        await registry.close()


class FakeHermesAdapter:
    def __init__(
        self,
        *,
        generation: str = "generation-1",
        creation: HermesSessionCreationResult | None = None,
        submission: ProviderSubmissionResult | None = None,
    ) -> None:
        self.facts = ProviderBindingFacts(
            provider="hermes",
            binding_id="hermes-default",
            supported_actions=frozenset({ProviderAction.LAUNCH, ProviderAction.SEND}),
            availability=ProviderAvailability(ready=True, generation=generation),
            durability=ProviderDurability(local_reservation=True, native_evidence=True),
        )
        self.creation = creation or HermesSessionCreated(
            runtime_session_id="runtime-1",
            stored_session_id="stored-1",
            effective_cwd="/effective",
        )
        self.submission = submission
        self.create_calls: list[dict[str, str]] = []
        self.submissions: list[PreparedProviderRequest] = []

    async def create_session(
        self, *, lane_id: str, cwd: str, title: str
    ) -> HermesSessionCreationResult:
        self.create_calls.append({"cwd": cwd, "title": title})
        creation = self.creation
        if isinstance(creation, HermesSessionCreated):
            return creation.__class__(
                runtime_session_id=creation.runtime_session_id,
                stored_session_id=creation.stored_session_id,
                effective_cwd=cwd,
            )
        return creation

    async def submit_prepared(self, request: PreparedProviderRequest) -> ProviderSubmissionResult:
        self.submissions.append(request)
        return self.submission or ProviderSubmissionAccepted(
            turn_id=f"turn-{len(self.submissions)}"
        )


def _hermes_ctx(store: Registry, adapter: FakeHermesAdapter) -> Ctx:
    ctx = make_ctx(store, FakeLaneClient())
    ctx.providers = ProviderRouter((adapter,))
    return ctx


def _router(ctx: Ctx) -> ProviderRouter:
    assert ctx.providers is not None
    return ctx.providers


async def test_hermes_plan_uses_cached_facts_without_provider_calls(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)

    plan = await handlers.plan_new_lane(
        NewInput(name="worker", cwd=str(tmp_path), provider="hermes", text="hello"), ctx
    )

    assert plan.provider == "hermes"
    assert plan.binding_id == "hermes-default"
    assert plan.provider_readiness == "ready"
    assert plan.provider_launch_supported is True
    assert plan.would_send is True
    assert plan.launch is None
    assert adapter.create_calls == []
    assert adapter.submissions == []
    assert await store.list_lanes() == []


async def test_hermes_launch_without_prompt_persists_separate_native_identities(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)

    result = await handlers.new_lane(
        NewInput(name="worker", cwd=str(tmp_path), provider="hermes", send=False), ctx
    )

    assert result.provider == "hermes"
    assert result.binding_id == "hermes-default"
    assert result.provider_session_id == "stored-1"
    assert result.status == "idle"
    assert result.launch is not None
    assert result.launch.status == "created"
    assert result.launch.runtime_session_id == "runtime-1"
    assert result.launch.stored_session_id == "stored-1"
    assert result.delivery is None
    assert result.message_accepted is False
    assert adapter.create_calls[0]["cwd"] == str(tmp_path)
    assert adapter.create_calls[0]["title"].endswith("worker")
    assert adapter.submissions == []


async def test_hermes_initial_prompt_is_a_reserved_correlated_delivery(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)

    result = await handlers.new_lane(
        NewInput(
            name="worker",
            cwd=str(tmp_path),
            provider="hermes",
            text="first",
            idempotency_key="launch-key",
        ),
        ctx,
    )

    assert result.launch is not None
    assert result.delivery is not None
    assert result.launch.first_delivery_id == result.delivery.id
    assert result.delivery.status == "accepted"
    assert result.delivery.turn_id == "turn-1"
    assert result.delivery.native_session_id == "stored-1"
    assert result.message_accepted is True
    request = adapter.submissions[0]
    assert request.correlation_id == result.delivery.id
    assert request.target.lane_id == result.id
    assert request.target.native_session_id == "stored-1"
    assert request.target.runtime_session_id == "runtime-1"
    assert request.target.generation == "generation-1"
    assert request.text == "first"


async def test_exact_launch_replay_precedes_changed_provider_defaults(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    (repo / ".dispatch").mkdir(parents=True)
    config = repo / ".dispatch" / "config.toml"
    config.write_text('[defaults]\nprovider = "hermes"\n')
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    request = NewInput(
        name="worker",
        cwd=str(repo),
        text="first",
        idempotency_key="launch-key",
    )

    first = await handlers.new_lane(request, ctx)
    config.write_text('[defaults]\nprovider = "codex"\nmodel = "changed"\n')
    replay = await handlers.new_lane(request, ctx)
    preview = await handlers.plan_new_lane(request, ctx)

    assert replay.id == first.id
    assert replay.launch == first.launch
    assert replay.delivery == first.delivery
    assert preview.launch == first.launch
    assert preview.provider == "hermes"
    assert len(adapter.create_calls) == 1
    assert len(adapter.submissions) == 1


async def test_changed_launch_intent_conflicts_before_provider_or_default_reads(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    await handlers.new_lane(
        NewInput(
            name="worker",
            cwd=str(tmp_path),
            provider="hermes",
            text="first",
            idempotency_key="launch-key",
        ),
        ctx,
    )

    with pytest.raises(DeliveryConflictError, match="different input"):
        await handlers.new_lane(
            NewInput(
                name="worker",
                cwd="/path/that/does/not/exist",
                provider="codex",
                text="changed",
                idempotency_key="launch-key",
            ),
            ctx,
        )

    assert len(adapter.create_calls) == 1
    assert len(adapter.submissions) == 1


async def test_delivery_key_blocks_new_and_plan_before_changed_defaults_or_binding(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    (repo / ".dispatch").mkdir(parents=True)
    config = repo / ".dispatch" / "config.toml"
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    lane = await handlers.new_lane(
        NewInput(name="worker", cwd=str(repo), provider="hermes", send=False), ctx
    )
    await handlers.send_message(
        SendInput(lane=lane.ref, text="first", idempotency_key="shared-key"), ctx
    )

    request = NewInput(name="other", cwd=str(repo), idempotency_key="shared-key")
    config.write_text("[defaults\ninvalid = true\n")
    for operation in (handlers.new_lane, handlers.plan_new_lane):
        with pytest.raises(DeliveryConflictError, match="bound to a delivery"):
            await operation(request, ctx)

    config.write_text('[defaults]\nprovider = "hermes"\n')
    ctx.providers = ProviderRouter(())
    for operation in (handlers.new_lane, handlers.plan_new_lane):
        with pytest.raises(DeliveryConflictError, match="bound to a delivery"):
            await operation(request, ctx)

    assert len(adapter.create_calls) == 1
    assert len(adapter.submissions) == 1


async def test_unknown_hermes_creation_is_held_without_an_automatic_retry(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter(creation=HermesSessionCreationUnknown(error="create response lost"))
    ctx = _hermes_ctx(store, adapter)
    request = NewInput(
        name="worker",
        cwd=str(tmp_path),
        provider="hermes",
        text="first",
        idempotency_key="launch-key",
    )

    with pytest.raises(AppServerError, match="outcome is unknown"):
        await handlers.new_lane(request, ctx)
    replay = await handlers.new_lane(request, ctx)

    assert replay.launch is not None
    assert replay.launch.status == "ambiguous"
    assert replay.delivery is None
    assert replay.status == "error"
    assert len(adapter.create_calls) == 1
    assert adapter.submissions == []


async def test_creation_key_on_codex_rejects_before_native_effects(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(ValidationError, match="only for Hermes"):
        await handlers.new_lane(
            NewInput(name="worker", cwd=str(tmp_path), idempotency_key="launch-key"), ctx
        )

    assert client.calls == []
    assert await store.list_lanes() == []


async def test_second_hermes_turn_uses_same_route_and_distinct_receipt(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    first = await handlers.new_lane(
        NewInput(
            name="worker",
            cwd=str(tmp_path),
            provider="hermes",
            text="first",
            idempotency_key="launch-key",
        ),
        ctx,
    )
    assert first.delivery is not None
    await apply_hermes_delivery_observation(
        store,
        _router(ctx),
        ProviderObservation(
            provider="hermes",
            binding_id="hermes-default",
            native_session_id="stored-1",
            kind="completed",
            correlation=ProviderCorrelation(
                delivery_id=first.delivery.id,
                correlation_id=first.delivery.id,
                native_run_id="turn-1",
            ),
            generation="generation-1",
            source="live",
            received_at=datetime.now(UTC),
        ),
    )
    await apply_hermes_activity_observation(
        store,
        _router(ctx),
        HermesSessionActivityObservation(
            lane_id=first.id,
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
            generation="generation-1",
            turn_id="turn-1",
            kind="terminal",
            observed_at=datetime.now(UTC),
        ),
    )
    assert (await store.get_lane(first.id)).status == "idle"

    second = await handlers.send_message(
        SendInput(lane=first.ref, text="second", idempotency_key="turn-two"), ctx
    )

    assert second.delivery is not None
    assert second.delivery.id != first.delivery.id
    assert second.delivery.turn_id == "turn-2"
    assert [request.text for request in adapter.submissions] == ["first", "second"]
    assert {request.target.lane_id for request in adapter.submissions} == {first.id}
    assert {request.target.native_session_id for request in adapter.submissions} == {"stored-1"}
    assert {request.target.runtime_session_id for request in adapter.submissions} == {"runtime-1"}
    assert {request.target.generation for request in adapter.submissions} == {"generation-1"}


async def test_unrelated_owned_session_activity_updates_readiness_without_settling_receipt(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    created = await handlers.new_lane(
        NewInput(name="worker", cwd=str(tmp_path), provider="hermes", text="local"), ctx
    )
    assert created.delivery is not None
    receipt_id = created.delivery.id

    async def activity(turn_id: str, kind: Literal["started", "terminal"]) -> bool:
        return await apply_hermes_activity_observation(
            store,
            _router(ctx),
            HermesSessionActivityObservation(
                lane_id=created.id,
                stored_session_id="stored-1",
                runtime_session_id="runtime-1",
                generation="generation-1",
                turn_id=turn_id,
                kind=kind,
                observed_at=datetime.now(UTC),
            ),
        )

    assert await activity("unrelated-b", "started")
    lane = await store.get_lane(created.id)
    assert lane.status == "busy"
    assert lane.active_turn_id == "unrelated-b"
    assert (await store.get_delivery(receipt_id)).status == "accepted"

    await apply_hermes_delivery_observation(
        store,
        _router(ctx),
        ProviderObservation(
            provider="hermes",
            binding_id="hermes-default",
            native_session_id="stored-1",
            kind="completed",
            correlation=ProviderCorrelation(
                delivery_id=receipt_id,
                correlation_id=receipt_id,
                native_run_id="unrelated-b",
            ),
            generation="generation-1",
            source="live",
            received_at=datetime.now(UTC),
        ),
    )
    assert (await store.get_delivery(receipt_id)).status == "accepted"

    assert not await activity("older-c", "terminal")
    lane = await store.get_lane(created.id)
    assert lane.status == "busy"
    assert lane.active_turn_id == "unrelated-b"
    assert (await store.get_delivery(receipt_id)).status == "accepted"

    assert await activity("unrelated-b", "terminal")
    lane = await store.get_lane(created.id)
    assert lane.status == "idle"
    assert lane.active_turn_id is None
    assert (await store.get_delivery(receipt_id)).status == "accepted"


async def test_activity_without_durable_launch_mapping_is_ignored_without_mutation(
    store: Registry,
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    lane = await store.add_lane(
        id="dsp_legacy_hermes",
        handle="@legacy",
        source="own",
        status="busy",
        provider="hermes",
        binding_id="hermes-default",
        provider_session_id="stored-legacy",
    )
    await store.set_active_turn(lane.id, "existing-turn")
    await store.upsert_lane_runtime_state(
        LaneRuntimeState(
            lane=lane.id,
            provider="hermes",
            binding_id="hermes-default",
            provider_thread_id="stored-legacy",
            status="waiting_input",
            active_turn_id="existing-turn",
            needs_attention=True,
            attention_kind="human_or_sensitive_input",
            attention_detail='{"members":[{"request_id":null}]}',
            updated_at=datetime.now(UTC).isoformat(),
        )
    )
    receipt, _ = await store.reserve_delivery(
        key="legacy-receipt",
        lane=lane.id,
        mode="send",
        submitted_payload="submitted",
        payload="{}",
        text="held",
        provider="hermes",
        binding_id="hermes-default",
        native_session_id="stored-legacy",
    )
    before_lane = await store.get_lane(lane.id)
    before_state = await store.get_lane_runtime_state(lane.id)
    before_receipt = await store.get_delivery(receipt.id)

    for kind in ("started", "terminal"):
        assert not await apply_hermes_activity_observation(
            store,
            _router(ctx),
            HermesSessionActivityObservation(
                lane_id=lane.id,
                stored_session_id="stored-legacy",
                runtime_session_id="untrusted-runtime",
                generation="generation-1",
                turn_id="untrusted-turn",
                kind=kind,
                observed_at=datetime.now(UTC),
            ),
        )

    assert await store.get_lane(lane.id) == before_lane
    assert await store.get_lane_runtime_state(lane.id) == before_state
    assert await store.get_delivery(receipt.id) == before_receipt


async def test_exact_replay_of_definite_rejection_never_submits_again(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter(
        submission=ProviderSubmissionRejected(error="Hermes gateway error 4091: busy")
    )
    ctx = _hermes_ctx(store, adapter)
    request = NewInput(
        name="worker",
        cwd=str(tmp_path),
        provider="hermes",
        text="first",
        idempotency_key="launch-key",
    )

    first = await handlers.new_lane(request, ctx)
    replay = await handlers.new_lane(request, ctx)

    assert first.delivery is not None
    assert first.delivery.status == "failed"
    assert replay.delivery == first.delivery
    assert len(adapter.submissions) == 1


async def test_lost_ack_stays_held_after_terminal_looking_session_activity(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter(
        submission=ProviderSubmissionUnknown(error="gateway closed before prompt ACK")
    )
    ctx = _hermes_ctx(store, adapter)
    request = NewInput(
        name="worker",
        cwd=str(tmp_path),
        provider="hermes",
        text="first",
        idempotency_key="launch-key",
    )

    first = await handlers.new_lane(request, ctx)
    assert first.delivery is not None
    assert first.delivery.status == "ambiguous"
    for kind in ("started", "terminal"):
        await apply_hermes_activity_observation(
            store,
            _router(ctx),
            HermesSessionActivityObservation(
                lane_id=first.id,
                stored_session_id="stored-1",
                runtime_session_id="runtime-1",
                generation="generation-1",
                turn_id="terminal-looking",
                kind=kind,
                observed_at=datetime.now(UTC),
            ),
        )

    replay = await handlers.new_lane(request, ctx)
    assert replay.delivery == first.delivery
    assert len(adapter.submissions) == 1
    with pytest.raises(CapabilityUnavailableError, match="unresolved delivery"):
        await handlers.send_message(
            SendInput(lane=first.ref, text="later", idempotency_key="later"), ctx
        )
    assert await store.get_delivery_by_key("later") is None
    assert len(adapter.submissions) == 1


async def test_current_known_attention_holds_only_its_hermes_lane(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    created = await handlers.new_lane(
        NewInput(name="worker", cwd=str(tmp_path), provider="hermes", send=False), ctx
    )
    observed_at = datetime.now(UTC)
    hold = HermesAttentionObservation(
        lane_id=created.id,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
        generation="generation-1",
        kind="secret.request",
        family="secret",
        request_id="request-1",
        category="human_or_sensitive_input",
        expired=False,
        observed_at=observed_at,
    )

    assert await apply_hermes_attention_observation(store, _router(ctx), hold) is True
    state = await store.get_lane_runtime_state(created.id)
    assert state is not None
    assert state.needs_attention is True
    assert state.attention_kind == "human_or_sensitive_input"
    assert state.status == "waiting_input"
    assert json.loads(state.attention_detail or "{}") == {
        "members": [
            {
                "category": "human_or_sensitive_input",
                "family": "secret",
                "kind": "secret.request",
                "observed_at": observed_at.isoformat(),
                "observed_runtime_sid": "runtime-1",
                "request_id": "request-1",
                "turn_attribution": "uncorrelated",
            }
        ]
    }
    with pytest.raises(CapabilityUnavailableError, match="attention hold"):
        await handlers.send_message(
            SendInput(lane=created.ref, text="blocked", idempotency_key="blocked"), ctx
        )
    assert await store.get_delivery_by_key("blocked") is None
    assert adapter.submissions == []


async def test_exact_current_expiry_clears_only_matching_expirable_attention(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    created = await handlers.new_lane(
        NewInput(name="worker", cwd=str(tmp_path), provider="hermes", send=False), ctx
    )

    def observation(
        *, generation: str, expired: bool, request_id: str
    ) -> HermesAttentionObservation:
        return HermesAttentionObservation(
            lane_id=created.id,
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
            generation=generation,
            kind="clarify.expire" if expired else "clarify.request",
            family="clarify",
            request_id=request_id,
            category="human_or_sensitive_input",
            expired=expired,
            observed_at=datetime.now(UTC),
        )

    assert await apply_hermes_attention_observation(
        store, _router(ctx), observation(generation="generation-1", expired=False, request_id="a")
    )
    assert not await apply_hermes_attention_observation(
        store, _router(ctx), observation(generation="old", expired=True, request_id="a")
    )
    assert not await apply_hermes_attention_observation(
        store, _router(ctx), observation(generation="generation-1", expired=True, request_id="b")
    )
    assert (await store.get_lane_runtime_state(created.id)).needs_attention is True  # type: ignore[union-attr]
    assert await apply_hermes_attention_observation(
        store, _router(ctx), observation(generation="generation-1", expired=True, request_id="a")
    )
    cleared = await store.get_lane_runtime_state(created.id)
    assert cleared is not None
    assert cleared.needs_attention is False
    assert cleared.status == "idle"


async def test_overlapping_and_unidentified_attention_members_remain_held_until_exact_expiry(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    created = await handlers.new_lane(
        NewInput(name="worker", cwd=str(tmp_path), provider="hermes", send=False), ctx
    )

    def observation(
        family: str, request_id: str | None, *, expired: bool = False
    ) -> HermesAttentionObservation:
        return HermesAttentionObservation(
            lane_id=created.id,
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
            generation="generation-1",
            kind=f"{family}.{'expire' if expired else 'request'}",
            family=family,
            request_id=request_id,
            category="human_or_sensitive_input",
            expired=expired,
            observed_at=datetime.now(UTC),
        )

    for member in (
        observation("clarify", "a"),
        observation("secret", None),
        observation("clarify", "b"),
    ):
        assert await apply_hermes_attention_observation(store, _router(ctx), member)

    assert await apply_hermes_attention_observation(
        store, _router(ctx), observation("clarify", "b", expired=True)
    )
    state = await store.get_lane_runtime_state(created.id)
    assert state is not None and state.needs_attention is True
    members = json.loads(state.attention_detail or "{}")["members"]
    assert {(member["family"], member["request_id"]) for member in members} == {
        ("clarify", "a"),
        ("secret", None),
    }

    assert await apply_hermes_attention_observation(
        store, _router(ctx), observation("clarify", "a", expired=True)
    )
    state = await store.get_lane_runtime_state(created.id)
    assert state is not None and state.needs_attention is True
    assert json.loads(state.attention_detail or "{}")["members"][0]["request_id"] is None


async def test_bounded_live_observations_feed_partial_public_transcript(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    created = await handlers.new_lane(
        NewInput(name="worker", cwd=str(tmp_path), provider="hermes", text="remember amber"),
        ctx,
    )
    assert created.delivery is not None
    delivery = created.delivery

    async def observe(role: str, text: str, status: str) -> None:
        assert await apply_hermes_transcript_observation(
            store,
            _router(ctx),
            HermesTranscriptObservation(
                lane_id=created.id,
                delivery_id=delivery.id,
                stored_session_id="stored-1",
                runtime_session_id="runtime-1",
                generation="generation-1",
                turn_id="turn-1",
                role=role,  # type: ignore[arg-type]
                text=text,
                status=status,
                observed_at=datetime.now(UTC),
            ),
        )

    await observe("user", "remember amber", "accepted")
    await observe("assistant", "I will remember amber.", "complete")

    transcript = await handlers.transcript(TranscriptInput(lane=created.ref), ctx)
    detail = await handlers.show(
        ShowInput(lane=created.ref, include_transcript=True, max_items=10), ctx
    )

    assert transcript.transcript_source == "live_observed"
    assert transcript.partial is True
    assert [item.text for item in transcript.items] == [
        "remember amber",
        "I will remember amber.",
    ]
    assert [item.text for item in detail.transcript] == [
        "remember amber",
        "I will remember amber.",
    ]
    assert detail.sync.transcript_partial is True
    assert detail.sync.history_source == "live_observed"
    assert detail.sync.history_complete is False
    assert detail.sync.history_capability == "unsupported"


async def test_generation_change_fences_existing_hermes_lane_before_provider_call(
    store: Registry, tmp_path: Path
) -> None:
    first_adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, first_adapter)
    created = await handlers.new_lane(
        NewInput(name="worker", cwd=str(tmp_path), provider="hermes", send=False), ctx
    )
    replacement = FakeHermesAdapter(generation="generation-2")
    ctx.providers = ProviderRouter((replacement,))

    with pytest.raises(CapabilityUnavailableError, match="generation changed"):
        await handlers.send_message(
            SendInput(lane=created.ref, text="second", idempotency_key="turn-two"), ctx
        )

    assert replacement.submissions == []
    receipt = await store.get_delivery_by_key("turn-two")
    assert receipt is None


async def test_replacement_generation_quarantines_old_lanes_but_allows_new_ones(
    store: Registry, tmp_path: Path
) -> None:
    first_adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, first_adapter)
    original_request = NewInput(
        name="old",
        cwd=str(tmp_path),
        provider="hermes",
        text="first",
        idempotency_key="old-launch",
    )
    original = await handlers.new_lane(original_request, ctx)

    replacement = FakeHermesAdapter(
        generation="generation-2",
        creation=HermesSessionCreated(
            runtime_session_id="runtime-2",
            stored_session_id="stored-2",
            effective_cwd=str(tmp_path),
        ),
    )
    await quarantine_stale_hermes_lanes(
        store,
        binding_id="hermes-default",
        current_generation="generation-2",
    )
    ctx.providers = ProviderRouter((replacement,))

    state = await store.get_lane_runtime_state(original.id)
    assert state is not None
    assert state.status == "waiting_tool"
    assert state.needs_attention is True
    assert state.attention_kind == "native_client_capability_unavailable"
    assert json.loads(state.attention_detail or "{}") == {
        "members": [],
        "quarantine": {
            "current_generation": "generation-2",
            "frozen_generation": "generation-1",
            "reason": "owned Hermes gateway generation changed",
            "source": "provider_generation",
        },
    }

    roster = await handlers.roster(RosterInput(), ctx)
    item = next(item for item in roster.lanes if item.id == original.id)
    assert item.provider_state.readiness == "ready"
    assert item.writable is False
    assert item.capabilities.send is False
    assert item.write_locked_reason == "owned Hermes gateway generation changed"

    detail = await handlers.show(ShowInput(lane=original.ref), ctx)
    assert detail.writable is False
    assert detail.capabilities.send is False
    assert detail.attention.held is True
    assert detail.attention.kind == "native_client_capability_unavailable"
    assert detail.attention.reason == "owned Hermes gateway generation changed"
    assert "attention_detail" not in detail.model_dump()

    replay = await handlers.new_lane(original_request, ctx)
    assert replay.launch == original.launch
    assert replay.delivery == original.delivery
    assert replay.writable is False
    assert replay.capabilities.send is False
    assert replay.write_locked_reason == "owned Hermes gateway generation changed"
    assert replacement.create_calls == []
    assert replacement.submissions == []

    with pytest.raises(CapabilityUnavailableError, match="attention hold"):
        await handlers.send_message(
            SendInput(lane=original.ref, text="blocked", idempotency_key="blocked"), ctx
        )
    assert await store.get_delivery_by_key("blocked") is None

    fresh = await handlers.new_lane(
        NewInput(name="fresh", cwd=str(tmp_path), provider="hermes", send=False), ctx
    )
    assert fresh.provider_session_id == "stored-2"
    assert fresh.status == "idle"
    fresh_item = next(
        item for item in (await handlers.roster(RosterInput(), ctx)).lanes if item.id == fresh.id
    )
    assert fresh_item.provider_state.readiness == "ready"
    assert fresh_item.writable is True
    assert fresh_item.capabilities.send is True


async def test_generation_quarantine_survives_reopen_and_preserves_existing_attention(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "registry.sqlite3"
    store = await Registry.open(db_path)
    original_request = NewInput(
        name="old",
        cwd=str(tmp_path),
        provider="hermes",
        idempotency_key="old-launch",
        text="first",
    )
    try:
        first_adapter = FakeHermesAdapter()
        ctx = _hermes_ctx(store, first_adapter)
        original = await handlers.new_lane(original_request, ctx)
        await apply_hermes_attention_observation(
            store,
            _router(ctx),
            HermesAttentionObservation(
                lane_id=original.id,
                stored_session_id="stored-1",
                runtime_session_id="runtime-1",
                generation="generation-1",
                kind="secret.request",
                family="secret",
                request_id="secret-1",
                category="human_or_sensitive_input",
                expired=False,
                observed_at=datetime.now(UTC),
            ),
        )
        missing = await store.add_lane(
            id="dsp_missing_launch",
            handle="@missing",
            source="own",
            status="idle",
            provider="hermes",
            binding_id="hermes-default",
            provider_session_id="stored-missing",
        )
        frozen_launch = await store.get_lane_launch(original.id)

        quarantined = await quarantine_stale_hermes_lanes(
            store,
            binding_id="hermes-default",
            current_generation="generation-2",
        )
        assert set(quarantined) == {original.id, missing.id}
    finally:
        await store.close()

    reopened = await Registry.open(db_path)
    try:
        replacement = FakeHermesAdapter(generation="generation-2")
        ctx = _hermes_ctx(reopened, replacement)
        assert await reopened.get_lane_launch(original.id) == frozen_launch

        state = await reopened.get_lane_runtime_state(original.id)
        assert state is not None
        assert state.status == "waiting_input"
        assert state.attention_kind == "human_or_sensitive_input"
        detail = json.loads(state.attention_detail or "{}")
        assert detail["members"][0]["request_id"] == "secret-1"
        assert detail["quarantine"]["frozen_generation"] == "generation-1"

        missing_state = await reopened.get_lane_runtime_state(missing.id)
        assert missing_state is not None
        assert missing_state.status == "waiting_tool"
        assert missing_state.needs_attention is True
        missing_detail = json.loads(missing_state.attention_detail or "{}")
        assert missing_detail["quarantine"]["frozen_generation"] is None
        assert missing_detail["quarantine"]["reason"] == (
            "Hermes launch mapping is missing or invalid"
        )
        missing_view = await handlers.show(ShowInput(lane=missing.ref), ctx)
        assert missing_view.writable is False
        assert missing_view.capabilities.send is False
        assert missing_view.attention.held is True
        assert missing_view.attention.kind == "native_client_capability_unavailable"
        assert missing_view.attention.reason == "Hermes launch mapping is missing or invalid"
        assert missing_view.write_locked_reason == missing_view.attention.reason

        replay = await handlers.new_lane(original_request, ctx)
        assert replay.id == original.id
        assert replay.delivery == original.delivery
        assert replacement.create_calls == []
        assert replacement.submissions == []
    finally:
        await reopened.close()


async def test_generation_quarantine_treats_malformed_launch_as_unprovable(
    store: Registry, tmp_path: Path
) -> None:
    adapter = FakeHermesAdapter()
    ctx = _hermes_ctx(store, adapter)
    lane = await handlers.new_lane(
        NewInput(name="malformed", cwd=str(tmp_path), provider="hermes", send=False), ctx
    )
    await store._conn.execute(
        "UPDATE lane_launches SET created_at = 'malformed' WHERE lane = ?", (lane.id,)
    )
    await store._conn.commit()

    quarantined = await quarantine_stale_hermes_lanes(
        store,
        binding_id="hermes-default",
        current_generation="generation-2",
    )

    assert quarantined == (lane.id,)
    state = await store.get_lane_runtime_state(lane.id)
    assert state is not None
    assert state.status == "waiting_tool"
    assert state.needs_attention is True
    assert state.attention_kind == "native_client_capability_unavailable"
    detail = json.loads(state.attention_detail or "{}")
    assert detail["quarantine"]["frozen_generation"] is None
    assert detail["quarantine"]["reason"] == "Hermes launch mapping is missing or invalid"
