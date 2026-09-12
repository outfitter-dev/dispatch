from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

from outfitter.dispatch.client.hermes import (
    HermesAttentionEvent,
    HermesClient,
    HermesEvent,
    HermesSessionCreated,
    HermesSubmissionAccepted,
    HermesTurnStream,
)
from outfitter.dispatch.core.hermes import (
    HermesAttentionObservation,
    HermesLaneAdapter,
    HermesSessionActivityObservation,
    HermesTranscriptObservation,
)
from outfitter.dispatch.core.providers import (
    PreparedProviderRequest,
    ProviderAction,
    ProviderSubmissionAccepted,
    ProviderSubmissionRejected,
    ProviderTarget,
)
from outfitter.dispatch.registry.observations import ProviderObservation


class StubHermesClient:
    def __init__(self, submission: HermesSubmissionAccepted) -> None:
        self.submission = submission
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.attention_handler: Callable[[HermesAttentionEvent], None] | None = None
        self.activity_handler: Callable[[HermesEvent], None] | None = None

    def set_attention_handler(self, handler: Callable[[HermesAttentionEvent], None]) -> None:
        self.attention_handler = handler

    def set_activity_handler(self, handler: Callable[[HermesEvent], None]) -> None:
        self.activity_handler = handler

    async def create_session(self, *, profile: str, cwd: str, title: str) -> HermesSessionCreated:
        self.calls.append(("create", {"profile": profile, "cwd": cwd, "title": title}))
        return HermesSessionCreated(
            runtime_session_id="runtime-1",
            stored_session_id="stored-1",
            effective_cwd=cwd,
        )

    async def submit_prompt(
        self, *, runtime_session_id: str, text: str
    ) -> HermesSubmissionAccepted:
        self.calls.append(("submit", {"runtime_session_id": runtime_session_id, "text": text}))
        return self.submission


def _stream(*events: HermesEvent, partial: bool = False) -> HermesTurnStream:
    return HermesTurnStream(
        tuple(events), partial=partial, max_live_events=8, on_close=lambda: None
    )


def _request(*, generation: str = "gen-1") -> PreparedProviderRequest:
    return PreparedProviderRequest(
        target=ProviderTarget(
            lane_id="dsp_lane",
            provider="hermes",
            binding_id="hermes-default",
            native_session_id="stored-1",
            runtime_session_id="runtime-1",
            generation=generation,
        ),
        action=ProviderAction.SEND,
        transport="turn",
        correlation_id="receipt-1",
        text="hello",
    )


async def test_adapter_maps_correlated_native_lifecycle() -> None:
    events = (
        HermesEvent(
            type="message.start",
            runtime_session_id="runtime-1",
            turn_id="turn-1",
            payload={"turn_id": "turn-1"},
        ),
        HermesEvent(
            type="message.complete",
            runtime_session_id="runtime-1",
            turn_id="turn-1",
            payload={"turn_id": "turn-1", "status": "complete", "text": "hi"},
        ),
    )
    client = StubHermesClient(HermesSubmissionAccepted("turn-1", _stream(*events)))
    observed: list[ProviderObservation] = []
    transcript: list[HermesTranscriptObservation] = []

    async def observe(event: ProviderObservation) -> object:
        observed.append(event)
        return event

    async def observe_transcript(event: HermesTranscriptObservation) -> object:
        transcript.append(event)
        return event

    adapter = HermesLaneAdapter(
        cast(HermesClient, client),
        generation="gen-1",
        observe=observe,
        observe_transcript=observe_transcript,
    )
    result = await adapter.submit_prepared(_request())
    assert isinstance(result, ProviderSubmissionAccepted)
    assert result.turn_id == "turn-1"
    await adapter.close_observers()

    assert [event.kind for event in observed] == ["started", "completed"]
    assert all(event.native_session_id == "stored-1" for event in observed)
    assert all(event.generation == "gen-1" for event in observed)
    assert [(item.role, item.text, item.turn_id) for item in transcript] == [
        ("user", "hello", "turn-1"),
        ("assistant", "hi", "turn-1"),
    ]


async def test_adapter_reports_buffer_overflow_as_accepted_but_partial() -> None:
    client = StubHermesClient(HermesSubmissionAccepted("turn-1", _stream(partial=True)))

    async def observe(_event: ProviderObservation) -> object:
        raise AssertionError("partial streams must not promote later evidence")

    adapter = HermesLaneAdapter(cast(HermesClient, client), generation="gen-1", observe=observe)
    result = await adapter.submit_prepared(_request())

    assert isinstance(result, ProviderSubmissionAccepted)
    assert result.evidence_partial is True
    assert result.uncertainty_reason is not None


async def test_adapter_rejects_stale_generation_before_provider_call() -> None:
    client = StubHermesClient(HermesSubmissionAccepted("turn-1", _stream()))

    async def observe(event: ProviderObservation) -> object:
        return event

    adapter = HermesLaneAdapter(cast(HermesClient, client), generation="gen-1", observe=observe)
    result = await adapter.submit_prepared(_request(generation="old"))

    assert isinstance(result, ProviderSubmissionRejected)
    assert client.calls == []


async def test_adapter_uses_fixed_default_profile_for_creation() -> None:
    client = StubHermesClient(HermesSubmissionAccepted("turn-1", _stream()))

    async def observe(event: ProviderObservation) -> object:
        return event

    adapter = HermesLaneAdapter(cast(HermesClient, client), generation="gen-1", observe=observe)
    created = await adapter.create_session(lane_id="dsp_lane", cwd="/work", title="Worker")

    assert isinstance(created, HermesSessionCreated)
    assert client.calls == [("create", {"profile": "default", "cwd": "/work", "title": "Worker"})]


async def test_adapter_scopes_turn_uncorrelated_attention_to_created_lane() -> None:
    client = StubHermesClient(HermesSubmissionAccepted("turn-1", _stream()))

    async def observe(event: ProviderObservation) -> object:
        return event

    attention: list[HermesAttentionObservation] = []

    async def observe_attention(event: HermesAttentionObservation) -> object:
        attention.append(event)
        return event

    adapter = HermesLaneAdapter(
        cast(HermesClient, client),
        generation="gen-1",
        observe=observe,
        observe_attention=observe_attention,
    )
    await adapter.create_session(lane_id="dsp_lane", cwd="/work", title="Worker")
    assert client.attention_handler is not None
    client.attention_handler(
        HermesAttentionEvent(
            type="clarify.request",
            family="clarify",
            runtime_session_id="runtime-1",
            request_id="request-1",
            category="human_or_sensitive_input",
            expired=False,
        )
    )
    await asyncio.sleep(0)

    assert len(attention) == 1
    assert attention[0].lane_id == "dsp_lane"
    assert attention[0].stored_session_id == "stored-1"
    assert attention[0].runtime_session_id == "runtime-1"
    assert attention[0].generation == "gen-1"


async def test_adapter_scopes_uncorrelated_activity_to_created_runtime_session() -> None:
    client = StubHermesClient(HermesSubmissionAccepted("turn-1", _stream()))

    async def observe(event: ProviderObservation) -> object:
        return event

    activity: list[HermesSessionActivityObservation] = []

    async def observe_activity(event: HermesSessionActivityObservation) -> object:
        activity.append(event)
        return event

    adapter = HermesLaneAdapter(
        cast(HermesClient, client),
        generation="gen-1",
        observe=observe,
        observe_activity=observe_activity,
    )
    await adapter.create_session(lane_id="dsp_lane", cwd="/work", title="Worker")
    assert client.activity_handler is not None
    client.activity_handler(
        HermesEvent(
            type="message.start",
            runtime_session_id="other-runtime",
            turn_id="unrelated-a",
            payload={"turn_id": "unrelated-a"},
        )
    )
    client.activity_handler(
        HermesEvent(
            type="message.start",
            runtime_session_id="runtime-1",
            turn_id="unrelated-b",
            payload={"turn_id": "unrelated-b"},
        )
    )
    await adapter.close_observers()

    assert len(activity) == 1
    assert activity[0].lane_id == "dsp_lane"
    assert activity[0].stored_session_id == "stored-1"
    assert activity[0].runtime_session_id == "runtime-1"
    assert activity[0].turn_id == "unrelated-b"
    assert activity[0].kind == "started"
