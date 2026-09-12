"""The narrow Hermes adapter projected onto Dispatch launch and delivery seams."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from outfitter.dispatch.client.hermes import (
    HermesAttentionEvent,
    HermesClient,
    HermesEvent,
    HermesSessionCreated,
    HermesSessionCreationResult,
    HermesSubmissionAccepted,
    HermesSubmissionRejected,
)
from outfitter.dispatch.config import DEFAULT_HERMES_BINDING_ID, DEFAULT_HERMES_PROFILE
from outfitter.dispatch.contracts.errors import NotFoundError
from outfitter.dispatch.registry.models import (
    LaneRuntimeState,
    LaneStatus,
    LaneSync,
    ThreadItem,
    ThreadTurn,
)
from outfitter.dispatch.registry.observations import ProviderCorrelation, ProviderObservation

from .providers import (
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

if TYPE_CHECKING:
    from outfitter.dispatch.registry.store import Registry

ObservationSink = Callable[[ProviderObservation], Awaitable[object]]
HERMES_ACTIONS = frozenset({ProviderAction.LAUNCH, ProviderAction.SEND})
HERMES_OBSERVED_TEXT_CHARS = 32_000
HERMES_ATTENTION_MEMBER_LIMIT = 64


@dataclass(frozen=True)
class HermesAttentionObservation:
    lane_id: str
    stored_session_id: str
    runtime_session_id: str
    generation: str
    kind: str
    family: str
    request_id: str | None
    category: str
    expired: bool
    observed_at: datetime


AttentionSink = Callable[[HermesAttentionObservation], Awaitable[object]]


@dataclass(frozen=True)
class HermesTranscriptObservation:
    lane_id: str
    delivery_id: str
    stored_session_id: str
    runtime_session_id: str
    generation: str
    turn_id: str
    role: Literal["user", "assistant"]
    text: str
    status: str
    observed_at: datetime
    truncated: bool = False


TranscriptSink = Callable[[HermesTranscriptObservation], Awaitable[object]]


@dataclass(frozen=True)
class HermesSessionActivityObservation:
    lane_id: str
    stored_session_id: str
    runtime_session_id: str
    generation: str
    turn_id: str
    kind: Literal["started", "terminal"]
    observed_at: datetime


ActivitySink = Callable[[HermesSessionActivityObservation], Awaitable[object]]


class HermesLaneAdapter:
    """Bind one negotiated Hermes gateway generation to durable Dispatch requests."""

    def __init__(
        self,
        client: HermesClient,
        *,
        generation: str,
        observe: ObservationSink,
        observe_attention: AttentionSink | None = None,
        observe_transcript: TranscriptSink | None = None,
        observe_activity: ActivitySink | None = None,
    ) -> None:
        self.client = client
        self._observe = observe
        self._observe_attention = observe_attention
        self._observe_transcript = observe_transcript
        self._observe_activity = observe_activity
        self._tasks: set[asyncio.Task[None]] = set()
        self._sessions: dict[str, tuple[str, str]] = {}
        self.facts = ProviderBindingFacts(
            provider="hermes",
            binding_id=DEFAULT_HERMES_BINDING_ID,
            supported_actions=HERMES_ACTIONS,
            availability=ProviderAvailability(ready=True, generation=generation),
            durability=ProviderDurability(local_reservation=True, native_evidence=True),
        )
        self.client.set_attention_handler(self._receive_attention)
        self.client.set_activity_handler(self._receive_activity)

    async def create_session(
        self, *, lane_id: str, cwd: str, title: str
    ) -> HermesSessionCreationResult:
        result = await self.client.create_session(
            profile=DEFAULT_HERMES_PROFILE,
            cwd=cwd,
            title=title,
        )
        if isinstance(result, HermesSessionCreated):
            self._sessions[result.runtime_session_id] = (lane_id, result.stored_session_id)
        return result

    async def submit_prepared(self, request: PreparedProviderRequest) -> ProviderSubmissionResult:
        target = request.target
        generation = self.facts.availability.generation
        if (
            target.provider != "hermes"
            or target.binding_id != DEFAULT_HERMES_BINDING_ID
            or target.runtime_session_id is None
            or target.generation is None
            or target.generation != generation
            or request.action is not ProviderAction.SEND
            or request.transport != "turn"
            or request.settings is not None
        ):
            return ProviderSubmissionRejected(error="reserved Hermes request has an invalid route")

        result = await self.client.submit_prompt(
            runtime_session_id=target.runtime_session_id,
            text=request.text,
        )
        if isinstance(result, HermesSubmissionRejected):
            return ProviderSubmissionRejected(error=result.error)
        if not isinstance(result, HermesSubmissionAccepted):
            return ProviderSubmissionUnknown(error=result.error)
        if result.partial:
            await result.stream.aclose()
        else:
            await self._emit_transcript(
                request,
                result.turn_id,
                role="user",
                text=request.text,
                status="accepted",
            )
            task = asyncio.create_task(
                self._observe_turn(request, result),
                name=f"hermes-turn:{target.runtime_session_id}:{result.turn_id}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return ProviderSubmissionAccepted(
            turn_id=result.turn_id,
            evidence_partial=result.partial,
            uncertainty_reason=result.uncertainty_reason,
        )

    async def close_observers(self) -> None:
        """Stop local observers after the client has closed their native streams."""

        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def _receive_attention(self, event: HermesAttentionEvent) -> None:
        if self._observe_attention is None:
            return
        route = self._sessions.get(event.runtime_session_id)
        if route is None:
            return
        lane_id, stored_session_id = route
        observation = HermesAttentionObservation(
            lane_id=lane_id,
            stored_session_id=stored_session_id,
            runtime_session_id=event.runtime_session_id,
            generation=self.facts.availability.generation or "",
            kind=event.type,
            family=event.family,
            request_id=event.request_id,
            category=event.category,
            expired=event.expired,
            observed_at=datetime.now(UTC),
        )
        task = asyncio.create_task(
            self._forward_attention(observation),
            name=f"hermes-attention:{event.runtime_session_id}:{event.family}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _forward_attention(self, observation: HermesAttentionObservation) -> None:
        assert self._observe_attention is not None
        await self._observe_attention(observation)

    def _receive_activity(self, event: HermesEvent) -> None:
        if self._observe_activity is None:
            return
        route = self._sessions.get(event.runtime_session_id)
        if route is None or event.type not in {"message.start", "message.complete"}:
            return
        lane_id, stored_session_id = route
        observation = HermesSessionActivityObservation(
            lane_id=lane_id,
            stored_session_id=stored_session_id,
            runtime_session_id=event.runtime_session_id,
            generation=self.facts.availability.generation or "",
            turn_id=event.turn_id,
            kind="started" if event.type == "message.start" else "terminal",
            observed_at=datetime.now(UTC),
        )
        task = asyncio.create_task(
            self._forward_activity(observation),
            name=f"hermes-activity:{event.runtime_session_id}:{event.turn_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _forward_activity(self, observation: HermesSessionActivityObservation) -> None:
        assert self._observe_activity is not None
        await self._observe_activity(observation)

    async def _observe_turn(
        self, request: PreparedProviderRequest, accepted: HermesSubmissionAccepted
    ) -> None:
        terminal = False
        try:
            for event in accepted.stream.buffered:
                terminal = await self._apply_event(request, accepted.turn_id, event) or terminal
            if terminal:
                await accepted.stream.aclose()
                return
            async for event in accepted.stream:
                terminal = await self._apply_event(request, accepted.turn_id, event) or terminal
                if terminal:
                    await accepted.stream.aclose()
                    break
        finally:
            if not terminal:
                await self._emit(
                    request,
                    accepted.turn_id,
                    "uncertain",
                    partial=True,
                    reason="Hermes gateway stream closed before terminal evidence",
                )

    async def _apply_event(
        self, request: PreparedProviderRequest, turn_id: str, event: HermesEvent
    ) -> bool:
        if (
            event.runtime_session_id != request.target.runtime_session_id
            or event.turn_id != turn_id
        ):
            return False
        if event.type == "message.start":
            await self._emit(request, turn_id, "started")
            return False
        if event.type == "message.complete":
            status = event.payload.get("status")
            text = event.payload.get("text")
            if isinstance(text, str):
                await self._emit_transcript(
                    request,
                    turn_id,
                    role="assistant",
                    text=text,
                    status=status if isinstance(status, str) else "unknown",
                )
            if status == "complete":
                await self._emit(request, turn_id, "completed")
            else:
                await self._emit(
                    request,
                    turn_id,
                    "failed",
                    reason=f"Hermes message completed with status {status!r}",
                )
            return True
        return False

    async def _emit(
        self,
        request: PreparedProviderRequest,
        turn_id: str,
        kind: str,
        *,
        partial: bool = False,
        reason: str | None = None,
    ) -> None:
        await self._observe(
            ProviderObservation.model_validate(
                {
                    "provider": "hermes",
                    "binding_id": request.target.binding_id,
                    "native_session_id": request.target.native_session_id,
                    "kind": kind,
                    "correlation": ProviderCorrelation(
                        delivery_id=request.correlation_id,
                        correlation_id=request.correlation_id,
                        native_run_id=turn_id,
                    ),
                    "generation": request.target.generation,
                    "source": "live",
                    "received_at": datetime.now(UTC),
                    "partial": partial,
                    "reason": reason,
                }
            )
        )

    async def _emit_transcript(
        self,
        request: PreparedProviderRequest,
        turn_id: str,
        *,
        role: Literal["user", "assistant"],
        text: str,
        status: str,
    ) -> None:
        if self._observe_transcript is None:
            return
        bounded = text[:HERMES_OBSERVED_TEXT_CHARS]
        await self._observe_transcript(
            HermesTranscriptObservation(
                lane_id=request.target.lane_id,
                delivery_id=request.correlation_id,
                stored_session_id=request.target.native_session_id,
                runtime_session_id=request.target.runtime_session_id or "",
                generation=request.target.generation or "",
                turn_id=turn_id,
                role=role,
                text=bounded,
                status=status,
                observed_at=datetime.now(UTC),
                truncated=len(bounded) != len(text),
            )
        )


async def apply_hermes_delivery_observation(
    registry: Registry,
    router: ProviderRouter,
    observation: ProviderObservation,
) -> object:
    """Reduce exact current-generation evidence into its receipt only."""

    correlation = observation.correlation
    if correlation.delivery_id is None or correlation.native_run_id is None:
        return await registry.apply_receipt_observation(observation)
    receipt = await registry.get_delivery(correlation.delivery_id)
    from .delivery import decode_prepared_request

    request = decode_prepared_request(receipt)
    facts = router.facts_for_binding(observation.provider, observation.binding_id)
    if (
        facts is None
        or not facts.availability.ready
        or observation.generation is None
        or facts.availability.generation != observation.generation
        or request.target.generation != observation.generation
    ):
        return None
    transition = await registry.apply_receipt_observation(observation)
    return transition


async def apply_hermes_activity_observation(
    registry: Registry,
    router: ProviderRouter,
    observation: HermesSessionActivityObservation,
) -> bool:
    """Reduce owned-session activity without correlating or settling a receipt."""

    facts = router.facts_for_binding("hermes", DEFAULT_HERMES_BINDING_ID)
    if (
        facts is None
        or not facts.availability.ready
        or facts.availability.generation != observation.generation
    ):
        return False
    lane = await registry.find_lane(observation.lane_id)
    if (
        lane is None
        or lane.source != "own"
        or lane.provider != "hermes"
        or lane.binding_id != DEFAULT_HERMES_BINDING_ID
        or lane.provider_session_id != observation.stored_session_id
    ):
        return False
    try:
        launch = await registry.get_lane_launch(lane.id)
    except NotFoundError:
        return False
    if (
        launch.generation != observation.generation
        or launch.runtime_session_id != observation.runtime_session_id
        or launch.stored_session_id != observation.stored_session_id
    ):
        return False
    if observation.kind == "started":
        await registry.record_lane_activity_started(lane.id, observation.turn_id)
        changed = True
    else:
        changed = await registry.record_lane_activity_idle_if_active(lane.id, observation.turn_id)
    if changed:
        await _sync_runtime_state(registry, lane.id, observation.observed_at.isoformat())
    return changed


async def apply_hermes_attention_observation(
    registry: Registry,
    router: ProviderRouter,
    observation: HermesAttentionObservation,
) -> bool:
    """Persist one audited blocking request as a lane hold without native control."""

    facts = router.facts_for_binding("hermes", DEFAULT_HERMES_BINDING_ID)
    if (
        facts is None
        or not facts.availability.ready
        or facts.availability.generation != observation.generation
    ):
        return False
    lane = await registry.find_lane(observation.lane_id)
    if (
        lane is None
        or lane.provider != "hermes"
        or lane.binding_id != DEFAULT_HERMES_BINDING_ID
        or lane.provider_session_id != observation.stored_session_id
    ):
        return False
    launch = await registry.get_lane_launch(lane.id)
    if (
        launch.generation != observation.generation
        or launch.runtime_session_id != observation.runtime_session_id
        or launch.stored_session_id != observation.stored_session_id
    ):
        return False
    current = await registry.get_lane_runtime_state(lane.id)
    members = _attention_members(current.attention_detail if current else None)
    if observation.expired:
        if observation.family == "approval":
            return False
        if observation.request_id is None or current is None or not current.needs_attention:
            return False
        matching = [
            member
            for member in members
            if member.get("family") == observation.family
            and member.get("request_id") == observation.request_id
            and member.get("observed_runtime_sid") == observation.runtime_session_id
        ]
        if not matching:
            return False
        members = [member for member in members if member not in matching]
        if members:
            status, attention_kind = _attention_summary(members)
            await registry.update_lane_status(lane.id, status)
            await registry.upsert_lane_runtime_state(
                current.model_copy(
                    update={
                        "status": status,
                        "attention_kind": attention_kind,
                        "attention_detail": _encode_attention_members(members),
                        "updated_at": observation.observed_at.isoformat(),
                        "last_event_at": observation.observed_at.isoformat(),
                    }
                )
            )
            return True
        expiry_status: LaneStatus = "busy" if current.active_turn_id else "idle"
        await registry.update_lane_status(lane.id, expiry_status)
        await registry.upsert_lane_runtime_state(
            current.model_copy(
                update={
                    "status": expiry_status,
                    "needs_attention": False,
                    "attention_kind": None,
                    "attention_detail": None,
                    "updated_at": observation.observed_at.isoformat(),
                    "last_event_at": observation.observed_at.isoformat(),
                }
            )
        )
        return True

    member: dict[str, object] = {
        "category": observation.category,
        "family": observation.family,
        "kind": observation.kind,
        "observed_at": observation.observed_at.isoformat(),
        "observed_runtime_sid": observation.runtime_session_id,
        "request_id": observation.request_id,
        "turn_attribution": "uncorrelated",
    }
    member_key = _attention_member_key(member)
    replaced = False
    for index, existing in enumerate(members):
        if _attention_member_key(existing) == member_key:
            members[index] = member
            replaced = True
            break
    if not replaced:
        if len(members) < HERMES_ATTENTION_MEMBER_LIMIT:
            members.append(member)
        elif not any(existing.get("overflow") is True for existing in members):
            # Retain every known member and one unexpirable sentinel. This keeps
            # the lane safely held when exact identities exceed the local bound.
            members.append(
                {
                    "category": "human_or_sensitive_input",
                    "family": "attention_overflow",
                    "kind": "attention.overflow",
                    "observed_at": observation.observed_at.isoformat(),
                    "observed_runtime_sid": observation.runtime_session_id,
                    "overflow": True,
                    "request_id": None,
                    "turn_attribution": "uncorrelated",
                }
            )
    status, attention_kind = _attention_summary(members)
    await registry.update_lane_status(lane.id, status)
    await registry.upsert_lane_runtime_state(
        LaneRuntimeState(
            lane=lane.id,
            provider=lane.provider,
            binding_id=lane.binding_id,
            provider_thread_id=observation.stored_session_id,
            status=status,
            active_turn_id=current.active_turn_id if current else lane.active_turn_id,
            latest_turn_id=current.latest_turn_id if current else lane.latest_turn_id,
            latest_turn_status=(current.latest_turn_status if current else lane.latest_turn_status),
            needs_attention=True,
            attention_kind=attention_kind,
            attention_detail=_encode_attention_members(members),
            updated_at=observation.observed_at.isoformat(),
            last_event_at=observation.observed_at.isoformat(),
        )
    )
    return True


async def apply_hermes_transcript_observation(
    registry: Registry,
    router: ProviderRouter,
    observation: HermesTranscriptObservation,
) -> bool:
    """Index bounded ACK/terminal text as partial live observation only."""

    facts = router.facts_for_binding("hermes", DEFAULT_HERMES_BINDING_ID)
    if (
        facts is None
        or not facts.availability.ready
        or facts.availability.generation != observation.generation
    ):
        return False
    receipt = await registry.get_delivery(observation.delivery_id)
    from .delivery import decode_prepared_request

    request = decode_prepared_request(receipt)
    target = request.target
    if (
        receipt.lane != observation.lane_id
        or receipt.correlation_id != observation.delivery_id
        or target.provider != "hermes"
        or target.binding_id != DEFAULT_HERMES_BINDING_ID
        or target.native_session_id != observation.stored_session_id
        or target.runtime_session_id != observation.runtime_session_id
        or target.generation != observation.generation
        or (receipt.turn_id is not None and receipt.turn_id != observation.turn_id)
    ):
        return False
    now = observation.observed_at.isoformat()
    existing_turns = await registry.list_thread_turns(lane=observation.lane_id, limit=500)
    new_turn = not any(turn.turn_id == observation.turn_id for turn in existing_turns)
    existing_item = await registry.find_thread_item(
        "hermes",
        observation.stored_session_id,
        f"hermes-observed-{observation.role}:{observation.turn_id}",
        binding_id=DEFAULT_HERMES_BINDING_ID,
    )
    terminal = observation.role == "assistant"
    turn_status: Literal["completed", "failed", "unknown"] = (
        "completed"
        if terminal and observation.status == "complete"
        else "failed"
        if terminal
        else "unknown"
    )
    await registry.upsert_thread_turn(
        ThreadTurn(
            provider="hermes",
            binding_id=DEFAULT_HERMES_BINDING_ID,
            provider_thread_id=observation.stored_session_id,
            turn_id=observation.turn_id,
            lane=observation.lane_id,
            status=turn_status,
            completed_at=now if turn_status == "completed" else None,
            failed_at=now if turn_status == "failed" else None,
            completion_source="live" if terminal else None,
            updated_at=now,
        )
    )
    await registry.upsert_thread_item(
        ThreadItem(
            provider="hermes",
            binding_id=DEFAULT_HERMES_BINDING_ID,
            provider_thread_id=observation.stored_session_id,
            item_id=f"hermes-observed-{observation.role}:{observation.turn_id}",
            lane=observation.lane_id,
            turn_id=observation.turn_id,
            item_type="userMessage" if observation.role == "user" else "agentMessage",
            role=observation.role,
            status=observation.status,
            text=observation.text,
            position=0 if observation.role == "user" else 1,
            inserted_at=now,
            payload={
                "source": "live_observed",
                "partial": True,
                "truncated": observation.truncated,
            },
            raw_retained=False,
        )
    )
    existing = await registry.get_lane_sync(observation.lane_id)
    await registry.upsert_lane_sync(
        LaneSync(
            lane=observation.lane_id,
            state="partial",
            last_synced_at=now,
            cwd=existing.cwd if existing else None,
            latest_event_at=now,
            latest_turn_id=observation.turn_id,
            transcript_partial=True,
            history_source="live_observed",
            history_complete=False,
            history_capability="unsupported",
            observation_enabled=True,
            turns_indexed=(existing.turns_indexed if existing else 0) + int(new_turn),
            items_indexed=(existing.items_indexed if existing else 0) + int(existing_item is None),
            scanned_bytes=(existing.scanned_bytes if existing else 0)
            + (len(observation.text.encode("utf-8")) if existing_item is None else 0),
            truncated=observation.truncated or (existing.truncated if existing else False),
        )
    )
    return True


async def _sync_runtime_state(registry: Registry, lane_id: str, observed_at: str) -> None:
    lane = await registry.get_lane(lane_id)
    current = await registry.get_lane_runtime_state(lane_id)
    if current is None:
        await registry.upsert_lane_runtime_state(
            LaneRuntimeState(
                lane=lane.id,
                provider=lane.provider,
                binding_id=lane.binding_id,
                provider_thread_id=lane.provider_session_id or lane.id,
                status=lane.status,
                active_turn_id=lane.active_turn_id,
                latest_turn_id=lane.latest_turn_id,
                latest_turn_status=lane.latest_turn_status,
                updated_at=observed_at,
                last_event_at=observed_at,
            )
        )
        return
    status = current.status if current.needs_attention else lane.status
    if current.needs_attention:
        await registry.update_lane_status(lane.id, status)
    await registry.upsert_lane_runtime_state(
        current.model_copy(
            update={
                "status": status,
                "active_turn_id": lane.active_turn_id,
                "latest_turn_id": lane.latest_turn_id,
                "latest_turn_status": lane.latest_turn_status,
                "updated_at": observed_at,
                "last_event_at": observed_at,
            }
        )
    )


def _attention_members(raw: str | None) -> list[dict[str, object]]:
    if raw is None:
        return []
    try:
        value: object = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, dict):
        return []
    raw_members = value.get("members")
    if not isinstance(raw_members, list):
        return []
    return [dict(member) for member in raw_members if isinstance(member, dict)]


def _attention_member_key(member: dict[str, object]) -> tuple[object, object, object]:
    return (
        member.get("family"),
        member.get("request_id"),
        member.get("observed_runtime_sid"),
    )


def _attention_summary(members: list[dict[str, object]]) -> tuple[LaneStatus, str]:
    human = any(member.get("category") == "human_or_sensitive_input" for member in members)
    if human:
        return "waiting_input", "human_or_sensitive_input"
    return "waiting_tool", "native_client_capability_unavailable"


def _encode_attention_members(members: list[dict[str, object]]) -> str:
    return json.dumps({"members": members}, sort_keys=True, separators=(",", ":"))
