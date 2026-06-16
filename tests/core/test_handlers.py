"""Stateful handler tests (the cases examples can't reach from a fresh ctx)."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from outfitter.dispatch.client.errors import AppServerError as ClientAppServerError
from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.client.events import LaneIdle, TurnFailed, TurnStarted
from outfitter.dispatch.client.models import (
    ApprovalPolicy,
    ApprovalsReviewer,
    Effort,
    Personality,
    ReasoningSummary,
    SandboxPolicy,
    ThreadGoal,
    ThreadInfo,
    ThreadSearchMatch,
    ThreadSearchResult,
    ThreadStatus,
)
from outfitter.dispatch.config import RuntimePolicy
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    AuthorityError,
    NotFoundError,
    StagingError,
    ValidationError,
)
from outfitter.dispatch.core import handlers
from outfitter.dispatch.core.models import (
    AttachInput,
    CompactInput,
    DiscoverInput,
    ForkInput,
    GoalClearInput,
    GoalGetInput,
    GoalSetInput,
    HistoryInput,
    InboxAckInput,
    InboxListInput,
    InboxReadInput,
    LaneInput,
    LaneRenameInput,
    LaneSyncInput,
    LaneTextInput,
    LogInput,
    ModelsInput,
    NewInput,
    OpenInput,
    RollbackInput,
    RosterInput,
    SearchInput,
    SendInput,
    ShowInput,
    StatusInput,
    SubscribeInput,
    SubscriptionListInput,
    ThreadTargetInput,
    TranscriptInput,
    WatchInput,
)
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Registry]:
    s = await Registry.open()
    try:
        yield s
    finally:
        await s.close()


async def test_open_then_send_owned_lane(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    ref = await handlers.open_lane(OpenInput(name="alpha", cwd="/w"), ctx)
    assert ref.id == "lane-1"
    assert ref.handle == "@alpha"
    ack = await handlers.send(LaneTextInput(lane="lane-1", text="ping"), ctx)
    assert ack.accepted is True
    assert any(name == "turn_start" and kw["thread_id"] == "lane-1" for name, kw in client.calls)


async def test_subscribe_defaults_to_current_thread_and_inbox_ack(store: Registry) -> None:
    ctx = make_ctx(store)
    target = await store.add_lane(id="target", handle="@target", source="own", status="idle")
    subscriber = await store.add_lane(
        id="subscriber", handle="@subscriber", source="own", status="idle"
    )

    created = await handlers.subscribe(
        SubscribeInput(target=target.ref, spec="delivery:inbox", caller_thread_id=subscriber.id),
        ctx,
    )

    assert created.target_ref == target.ref
    assert created.subscriber_ref == subscriber.ref
    assert created.when == "done"
    assert created.delivery == "inbox"

    message = await store.add_inbox_message(
        recipient_lane=subscriber.id,
        source_lane=target.id,
        subscription_id=created.id,
        kind="subscription_update",
        subject="@target done",
        body="finished",
    )
    listed = await handlers.inbox_list(
        InboxListInput(caller_thread_id=subscriber.id, limit=10), ctx
    )
    assert [item.id for item in listed.messages] == [message.id]
    read = await handlers.inbox_read(InboxReadInput(id=message.id), ctx)
    assert read.source_ref == target.ref

    acked = await handlers.inbox_ack(InboxAckInput(id=message.id), ctx)
    assert acked.acked == 1
    assert acked.message is not None
    assert acked.message.state == "acked"


async def test_new_lane_can_create_subscription_to_launcher(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "dispatch"
    repo.mkdir()
    launcher = await store.add_lane(id="launcher", handle="@launcher", source="own", status="idle")
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="worker",
            cwd=str(repo),
            send=False,
            subscribe="when:done,delivery:inbox",
            caller_thread_id=launcher.id,
        ),
        ctx,
    )

    assert out.subscription is not None
    assert out.subscription.subscriber_ref == launcher.ref
    assert out.subscription.target_ref == out.ref
    listed = await handlers.subscription_list(SubscriptionListInput(subscriber=launcher.ref), ctx)
    assert [sub.id for sub in listed.subscriptions] == [out.subscription.id]


async def test_subscribe_default_falls_back_to_inbox_for_attached_subscriber(
    store: Registry,
) -> None:
    ctx = make_ctx(store)
    target = await store.add_lane(id="target", handle="@target", source="own", status="idle")
    subscriber = await store.add_lane(
        id="subscriber", handle="@subscriber", source="attached", status="idle"
    )

    created = await handlers.subscribe(
        SubscribeInput(target=target.ref, spec="default", caller_thread_id=subscriber.id),
        ctx,
    )

    assert created.delivery == "inbox"
    assert created.subscriber_ref == subscriber.ref


async def test_subscribe_explicit_turn_requires_writable_subscriber(store: Registry) -> None:
    ctx = make_ctx(store)
    target = await store.add_lane(id="target", handle="@target", source="own", status="idle")
    subscriber = await store.add_lane(
        id="subscriber", handle="@subscriber", source="attached", status="idle"
    )

    with pytest.raises(AuthorityError, match="turn delivery requires a writable subscriber"):
        await handlers.subscribe(
            SubscribeInput(
                target=target.ref,
                spec="delivery:turn",
                caller_thread_id=subscriber.id,
            ),
            ctx,
        )


async def test_subscribe_rejects_invalid_compact_spec(store: Registry) -> None:
    ctx = make_ctx(store)
    target = await store.add_lane(id="target", handle="@target", source="own", status="idle")
    subscriber = await store.add_lane(
        id="subscriber", handle="@subscriber", source="own", status="idle"
    )

    with pytest.raises(ValidationError, match="delivery must be turn or inbox"):
        await handlers.subscribe(
            SubscribeInput(
                target=target.ref,
                spec="delivery:maybe",
                caller_thread_id=subscriber.id,
            ),
            ctx,
        )


async def test_new_lane_sets_name_and_sends_initial_turn(store: Registry, tmp_path: Path) -> None:
    repo = tmp_path / "dispatch"
    repo.mkdir()
    (repo / ".git").mkdir()
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="builder",
            cwd=str(repo),
            text="start",
            sandbox="workspace-write",
            approval_policy="on-request",
            effort="low",
            model="gpt-5.5",
            service_tier="priority",
            developer_instructions="stay focused",
        ),
        ctx,
    )

    assert out.handle == "@[dispatch] builder"
    assert out.message_accepted is True
    assert out.goal_set is False
    assert out.latest_turn.status is None
    assert any(
        name == "thread_start"
        and kw["sandbox"] == "workspace-write"
        and kw["approval_policy"] == "on-request"
        and kw["model"] == "gpt-5.5"
        and kw["developer_instructions"] == "stay focused"
        for name, kw in client.calls
    )
    assert any(
        name == "thread_set_name" and kw["display_name"] == "[dispatch] builder"
        for name, kw in client.calls
    )
    assert any(
        name == "turn_start"
        and kw["text"] == "start"
        and kw["sandbox_policy"] == {"type": "workspaceWrite"}
        and kw["effort"] == "low"
        and kw["service_tier"] == "priority"
        for name, kw in client.calls
    )


async def test_new_lane_omits_policy_fields_to_inherit_codex_config(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "dispatch"
    repo.mkdir()
    (repo / ".git").mkdir()
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(NewInput(name="builder", cwd=str(repo), text="start"), ctx)

    assert out.message_accepted is True
    assert any(
        name == "thread_start" and kw["sandbox"] is None and kw["approval_policy"] is None
        for name, kw in client.calls
    )
    assert any(
        name == "turn_start" and kw["sandbox_policy"] is None and kw["approval_policy"] is None
        for name, kw in client.calls
    )
    settings = await store.get_lane_runtime_settings(out.id)
    assert settings is not None
    assert settings.sandbox is None
    assert settings.approval_policy is None


async def test_new_lane_resolves_fast_service_tier_alias_and_records_provenance(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="fast-worker",
            cwd=str(tmp_path),
            text="start",
            model="gpt-5.5",
            service_tier="fast",
        ),
        ctx,
    )

    assert out.model.model == "gpt-5.5"
    assert out.model.service_tier.requested == "fast"
    assert out.model.service_tier.resolved == "priority"
    assert out.model.service_tier.name == "Fast"
    assert out.model.service_tier.source == "dispatch"
    assert any(
        name == "thread_start" and kw["service_tier"] == "priority" for name, kw in client.calls
    )
    assert any(
        name == "turn_start" and kw["service_tier"] == "priority" for name, kw in client.calls
    )
    stored = await store.get_lane_model_settings(out.id)
    assert stored is not None
    assert stored.requested_service_tier == "fast"
    assert stored.resolved_service_tier == "priority"
    assert stored.service_tier_source == "dispatch"


async def test_new_lane_without_model_override_preserves_codex_default_call_shape(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(name="default-worker", cwd=str(tmp_path), send=False), ctx
    )

    assert out.model.model == "gpt-5.5"
    assert out.model.service_tier.source == "unknown"
    call = next(kw for name, kw in client.calls if name == "thread_start")
    assert call["model"] is None
    assert call["model_provider"] is None
    assert call["service_tier"] is None


async def test_new_lane_rejects_unadvertised_service_tier_with_catalog_guidance(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(ValidationError, match="available service tiers: auto, default"):
        await handlers.new_lane(
            NewInput(
                name="spark",
                cwd=str(tmp_path),
                model="gpt-5.3-codex-spark",
                service_tier="fast",
                send=False,
            ),
            ctx,
        )


async def test_models_refreshes_catalog_and_reports_fast_alias(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    refreshed = await handlers.models(ModelsInput(), ctx)
    cached = await handlers.models(ModelsInput(refresh=False), ctx)

    assert refreshed.source == "app-server"
    assert refreshed.configured_default.model == "gpt-5.5"
    assert refreshed.models[0].id == "gpt-5.5"
    assert refreshed.models[0].aliases == {"fast": "priority"}
    assert refreshed.models[0].service_tiers[0].id == "priority"
    assert cached.source == "registry"
    cached_by_id = {model.id: model for model in cached.models}
    assert cached_by_id["gpt-5.5"].aliases == {"fast": "priority"}
    assert [name for name, _ in client.calls].count("model_list") == 1


async def test_models_no_refresh_empty_catalog_reports_hint(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.models(ModelsInput(refresh=False), ctx)

    assert out.source == "registry"
    assert out.catalog_state == "empty"
    assert out.models == []
    assert (
        out.hint
        == "run dispatch models without --no-refresh to refresh the App Server model catalog"
    )
    assert "model_list" not in [name for name, _ in client.calls]


async def test_new_lane_no_send_registers_without_turn(store: Registry, tmp_path: Path) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(name="idle", cwd=str(tmp_path), text="do not send", send=False), ctx
    )

    assert out.message_accepted is False
    assert (await store.find_lane("lane-1")) is not None
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_send_reuses_runtime_settings_from_no_send_lane(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    out = await handlers.new_lane(
        NewInput(
            name="worker",
            cwd=str(tmp_path),
            text="later",
            send=False,
            sandbox="workspace-write",
            approval_policy="on-request",
            approvals_reviewer="user",
            effort="low",
            summary="concise",
            model="gpt-5.5",
            service_tier="priority",
            personality="pragmatic",
        ),
        ctx,
    )
    client.calls.clear()

    await handlers.send(LaneTextInput(lane=out.ref, text="start now"), ctx)

    call = next(kw for name, kw in client.calls if name == "turn_start")
    assert call["sandbox_policy"] == {"type": "workspaceWrite"}
    assert call["approval_policy"] == "on-request"
    assert call["approvals_reviewer"] == "user"
    assert call["effort"] == "low"
    assert call["summary"] == "concise"
    assert call["model"] == "gpt-5.5"
    assert call["service_tier"] == "priority"
    assert call["personality"] == "pragmatic"


async def test_queue_reuses_runtime_settings_from_no_send_lane(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    out = await handlers.new_lane(
        NewInput(
            name="queued-worker",
            cwd=str(tmp_path),
            text="later",
            send=False,
            sandbox="workspace-write",
            approval_policy="on-request",
        ),
        ctx,
    )
    client.calls.clear()

    ack = await handlers.send_message(SendInput(lane=out.ref, text="queued", mode="queue"), ctx)

    assert ack.op == "queue"
    call = next(kw for name, kw in client.calls if name == "turn_start")
    assert call["sandbox_policy"] == {"type": "workspaceWrite"}
    assert call["approval_policy"] == "on-request"


async def test_interject_reuses_runtime_settings_from_no_send_lane(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    out = await handlers.new_lane(
        NewInput(
            name="interject-worker",
            cwd=str(tmp_path),
            text="later",
            send=False,
            sandbox="workspace-write",
            approval_policy="on-request",
        ),
        ctx,
    )
    await store.set_active_turn(out.id, "turn-1")
    await store.update_lane_status(out.id, "busy")
    client.calls.clear()

    ack = await handlers.send_message(
        SendInput(lane=out.ref, text="replace", mode="interject"), ctx
    )

    assert ack.op == "interject"
    assert any(name == "turn_interrupt" and kw["turn_id"] == "turn-1" for name, kw in client.calls)
    call = next(kw for name, kw in client.calls if name == "turn_start")
    assert call["sandbox_policy"] == {"type": "workspaceWrite"}
    assert call["approval_policy"] == "on-request"


async def test_new_lane_sets_native_goal_before_initial_turn(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(name="goal-worker", cwd=str(tmp_path), goal="Loop until green.", text="Begin."),
        ctx,
    )

    assert out.goal_set is True
    assert out.message_accepted is True
    goal_index = [name for name, _ in client.calls].index("thread_goal_set")
    turn_index = [name for name, _ in client.calls].index("turn_start")
    assert goal_index < turn_index
    assert any(
        name == "thread_goal_set" and kw["objective"] == "Loop until green."
        for name, kw in client.calls
    )


async def test_new_lane_rejects_goal_slash_command_text_without_native_goal(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(ValidationError, match="slash commands are not interpreted"):
        await handlers.new_lane(
            NewInput(name="bad-goal", cwd=str(tmp_path), text="/goal ship"), ctx
        )

    assert not client.calls


class _FailingTurnClient(FakeLaneClient):
    async def turn_start(self, *args: object, **kwargs: object) -> dict[str, object]:
        self._record("turn_start", failed=True)
        raise TransportError("boom")


class _CompletingBeforeReturnClient(FakeLaneClient):
    def __init__(self, store: Registry) -> None:
        super().__init__()
        self._store = store

    async def turn_start(
        self,
        thread_id: str,
        text: str,
        cwd: str,
        approval_policy: ApprovalPolicy | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        effort: Effort | None = None,
        summary: ReasoningSummary | None = None,
        model: str | None = None,
        service_tier: str | None = None,
        output_schema: dict[str, object] | None = None,
        personality: Personality | None = None,
    ) -> dict[str, object]:
        await super().turn_start(
            thread_id,
            text,
            cwd,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            sandbox_policy=sandbox_policy,
            effort=effort,
            summary=summary,
            model=model,
            service_tier=service_tier,
            output_schema=output_schema,
            personality=personality,
        )
        await self._store.record_turn_started(thread_id, "turn-race")
        await self._store.record_turn_completed(thread_id, "turn-race")
        return {}


async def test_new_lane_initial_send_failure_leaves_lane_registered(
    store: Registry, tmp_path: Path
) -> None:
    client = _FailingTurnClient()
    ctx = make_ctx(store, client)

    with pytest.raises(TransportError):
        await handlers.new_lane(NewInput(name="still-here", cwd=str(tmp_path), text="boom"), ctx)

    lane = await store.find_lane("lane-1")
    assert lane is not None
    assert lane.status == "error"
    assert lane.latest_turn_id is None
    assert lane.latest_turn_status == "failed"
    assert lane.latest_error == "boom"
    log = await handlers.show_log(LogInput(limit=10), ctx)
    send_records = [record for record in log.actions if record.op == "send"]
    assert send_records
    assert send_records[0].outcome == "app_server"


async def test_send_resolves_by_handle(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    ref = await handlers.open_lane(OpenInput(name="beta"), ctx)
    ack = await handlers.send(LaneTextInput(lane="@beta", text="hi"), ctx)
    assert ack.lane == "lane-1"
    by_ref = await handlers.send(LaneTextInput(lane=ref.ref, text="again"), ctx)
    assert by_ref.lane == "lane-1"


async def test_send_does_not_overwrite_fast_completion_with_busy(store: Registry) -> None:
    client = _CompletingBeforeReturnClient(store)
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)

    await handlers.send(LaneTextInput(lane="@beta", text="fast"), ctx)

    detail = await handlers.show(ShowInput(lane="@beta"), ctx)
    assert detail.status == "idle"
    assert detail.active_turn_id is None
    assert detail.latest_turn.id == "turn-race"
    assert detail.latest_turn.status == "completed"


async def test_send_failure_marks_latest_turn_error(store: Registry) -> None:
    client = _FailingTurnClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)

    with pytest.raises(TransportError):
        await handlers.send(LaneTextInput(lane="@beta", text="boom"), ctx)

    detail = await handlers.show(ShowInput(lane="@beta"), ctx)
    assert detail.status == "error"
    assert detail.latest_turn.id is None
    assert detail.latest_turn.status == "failed"
    assert detail.latest_turn.error == "boom"


async def test_send_modes_context_and_interject(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)
    await store.set_active_turn("lane-1", "turn-1")

    context = await handlers.send_message(SendInput(lane="@beta", text="note", mode="context"), ctx)
    interject = await handlers.send_message(
        SendInput(lane="@beta", text="replace", mode="interject"), ctx
    )

    assert context.op == "brief"
    assert interject.op == "interject"
    assert any(name == "inject_items" for name, _ in client.calls)
    assert any(
        name == "turn_interrupt" and kw["thread_id"] == "lane-1" and kw["turn_id"] == "turn-1"
        for name, kw in client.calls
    )
    assert any(name == "turn_start" and kw["text"] == "replace" for name, kw in client.calls)
    assert (await store.get_lane("lane-1")).status == "busy"


async def test_send_intro_prepends_managed_sender_from_codex_thread_id(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="target"), ctx)
    client.next_thread_id = "lane-2"
    sender = await handlers.open_lane(OpenInput(name="Dispatch"), ctx)
    monkeypatch.setenv("CODEX_THREAD_ID", sender.id)

    ack = await handlers.send_message(SendInput(lane="@target", text="hello", intro=True), ctx)

    assert ack.lane == "lane-1"
    sent = next(kw["text"] for name, kw in client.calls if name == "turn_start")
    assert sent == (
        f'[dispatch] From @Dispatch ({sender.ref}). Use `dispatch send {sender.ref} "..."` '
        "to reply.\n\nhello"
    )


async def test_send_intro_requires_codex_thread_id(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(store, FakeLaneClient())
    await handlers.open_lane(OpenInput(name="target"), ctx)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)

    with pytest.raises(ValidationError, match="CODEX_THREAD_ID"):
        await handlers.send_message(SendInput(lane="@target", text="hello", intro=True), ctx)


async def test_send_intro_requires_managed_sender(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(store, FakeLaneClient())
    await handlers.open_lane(OpenInput(name="target"), ctx)
    monkeypatch.setenv("CODEX_THREAD_ID", "unknown-thread")

    with pytest.raises(ValidationError, match="managed by dispatch"):
        await handlers.send_message(SendInput(lane="@target", text="hello", intro=True), ctx)


async def test_send_intro_applies_to_queued_delivery(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="target"), ctx)
    await store.update_lane_status("lane-1", "busy")
    client.next_thread_id = "lane-2"
    sender = await handlers.open_lane(OpenInput(name="Dispatch"), ctx)
    monkeypatch.setenv("CODEX_THREAD_ID", sender.id)

    ack = await handlers.send_message(
        SendInput(lane="@target", text="later", mode="queue", intro=True), ctx
    )

    assert ack.op == "queue"
    queued = await store.next_pending_message("lane-1")
    assert queued is not None
    assert queued.text.startswith(f"[dispatch] From @Dispatch ({sender.ref}).")
    assert queued.text.endswith("\n\nlater")


async def test_send_queue_persists_when_lane_is_busy(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)
    await store.update_lane_status("lane-1", "busy")

    ack = await handlers.send_message(SendInput(lane="@beta", text="later", mode="queue"), ctx)

    assert ack.op == "queue"
    assert "pending=1" in (ack.detail or "")
    queued = await store.next_pending_message("lane-1")
    assert queued is not None
    assert queued.text == "later"
    assert not any(name == "turn_start" and kw["text"] == "later" for name, kw in client.calls)


async def test_send_queue_starts_immediately_when_lane_is_idle(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)

    ack = await handlers.send_message(SendInput(lane="@beta", text="now", mode="queue"), ctx)

    assert ack.op == "queue"
    assert "pending=0" in (ack.detail or "")
    assert await store.next_pending_message("lane-1") is None
    assert (await store.get_lane("lane-1")).status == "busy"
    sent = await store.get_queued_message(1)
    assert sent.status == "sent"
    assert any(name == "turn_start" and kw["text"] == "now" for name, kw in client.calls)


async def test_lane_rename_updates_registry_and_owned_thread_name(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="old"), ctx)

    out = await handlers.rename_lane(LaneRenameInput(old="@old", new="new"), ctx)

    assert out.handle == "@new"
    assert await store.find_lane_by_handle("@old") is None
    assert (await store.get_lane("lane-1")).handle == "@new"
    assert any(
        name == "thread_set_name" and kw["thread_id"] == "lane-1" and kw["display_name"] == "new"
        for name, kw in client.calls
    )


async def test_lane_rename_updates_attached_thread_name(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")

    out = await handlers.rename_lane(LaneRenameInput(old="@desktop", new="renamed"), ctx)

    assert out.handle == "@renamed"
    assert out.source == "attached"
    assert any(
        name == "thread_set_name" and kw["thread_id"] == "D1" and kw["display_name"] == "renamed"
        for name, kw in client.calls
    )


async def test_lane_rename_can_target_unmanaged_thread(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.rename_lane(LaneRenameInput(old="raw-thread", new="Raw Name"), ctx)

    assert out.id == "raw-thread"
    assert out.managed is False
    assert out.source == "unmanaged"
    assert (await handlers.roster(RosterInput(include_archived=True), ctx)).lanes == []
    assert any(
        name == "thread_set_name"
        and kw["thread_id"] == "raw-thread"
        and kw["display_name"] == "Raw Name"
        for name, kw in client.calls
    )


async def test_unresolved_handle_does_not_fall_through_as_raw_thread_id(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(NotFoundError):
        await handlers.rename_lane(LaneRenameInput(old="@missing", new="new"), ctx)

    assert not client.calls


async def test_show_can_include_compact_transcript(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "turn-1",
                    "items": [
                        {
                            "id": "u1",
                            "type": "userMessage",
                            "content": [{"type": "text", "text": "hello"}],
                        },
                        {"id": "a1", "type": "agentMessage", "text": "hi"},
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.show(ShowInput(lane="@alpha", include_transcript=True, max_items=2), ctx)

    assert [item.text for item in out.transcript] == ["hello", "hi"]
    assert any(
        name == "thread_read" and kw["thread_id"] == "lane-1" and kw["include_turns"] is True
        for name, kw in client.calls
    )


async def test_transcript_reads_persisted_turn_items(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [{"id": "a1", "type": "agentMessage", "text": "done"}],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.transcript(TranscriptInput(lane="lane-1", limit=1), ctx)

    assert out.lane == "lane-1"
    assert len(out.items) == 1
    assert out.items[0].text == "done"


async def test_history_overview_summarizes_managed_threads(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [
                        {"id": "u1", "type": "userMessage", "text": "run status"},
                        {
                            "id": "tool-1",
                            "type": "toolCall",
                            "toolName": "bash",
                            "text": "git status",
                        },
                        {
                            "id": "file-1",
                            "type": "fileChange",
                            "path": "src/app.py",
                            "text": "edited app",
                        },
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha", cwd="/tmp/no-such-history-worktree"), ctx)

    out = await handlers.history(HistoryInput(), ctx)

    assert out.mode == "overview"
    assert len(out.threads) == 1
    summary = out.threads[0]
    assert summary.ref == "0BGeK1"
    assert summary.turns == 1
    assert summary.items == 3
    assert summary.messages == 1
    assert summary.tool_calls == 1
    assert summary.unique_tools == ["bash"]
    assert summary.files_changed_count == 1
    assert summary.files_changed[0].path == "src/app.py"
    assert summary.transcript_bytes is not None


async def test_history_thread_views_filter_items_and_rollups(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [
                        {"id": "a1", "type": "agentMessage", "text": "I will inspect."},
                        {
                            "id": "b1",
                            "type": "toolCall",
                            "toolName": "bash",
                            "text": "git status",
                        },
                        {
                            "id": "p1",
                            "type": "toolCall",
                            "toolName": "apply_patch",
                            "path": "src/app.py",
                            "text": "patch src/app.py",
                        },
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    summary = await handlers.history(HistoryInput(lane="@alpha"), ctx)
    tools = await handlers.history(HistoryInput(lane="@alpha", view="tools"), ctx)
    files = await handlers.history(HistoryInput(lane="@alpha", view="files"), ctx)
    items = await handlers.history(
        HistoryInput(lane="@alpha", view="items", tool="bash", raw=True), ctx
    )

    assert summary.mode == "summary"
    assert summary.thread is not None
    assert summary.thread.tool_calls == 2
    assert [tool.tool for tool in tools.tools] == ["bash", "apply_patch"]
    assert files.files[0].path == "src/app.py"
    assert len(items.items) == 1
    assert items.items[0].tool == "bash"
    assert items.items[0].raw is not None


async def test_watch_collects_bounded_raw_events(store: Registry) -> None:
    client = FakeLaneClient()
    client.raw_log = [
        {"method": "turn/started", "params": {"threadId": "lane-1", "turnId": "t1"}},
        {"id": 7, "method": "item/tool/requestUserInput", "params": {"threadId": "lane-1"}},
    ]
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.watch(WatchInput(lane="lane-1", limit=2, timeout=1), ctx)

    assert out.timed_out is False
    assert [event.method for event in out.events] == ["turn/started", "item/tool/requestUserInput"]
    assert out.events[1].request_id == 7


async def test_watch_zero_timeout_returns_immediately(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.watch(WatchInput(lane="lane-1", timeout=0), ctx)

    assert out.events == []
    assert out.timed_out is True


async def test_reactor_persists_turn_failure_for_get(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)
    reactor = Reactor(ctx, runner=TriggerRunner(ctx, now=lambda: store._now()))

    await reactor.handle(TurnStarted("lane-1", "turn-1"))
    await reactor.handle(TurnFailed("lane-1", "turn-1", "unsupported model"))
    await reactor.handle(LaneIdle("lane-1"))

    out = await handlers.show(ShowInput(lane="lane-1"), ctx)
    assert out.status == "error"
    assert out.latest_turn.id == "turn-1"
    assert out.latest_turn.status == "failed"
    assert out.latest_turn.error == "unsupported model"


async def test_goal_get_set_and_clear_use_native_goal_api(store: Registry) -> None:
    client = FakeLaneClient()
    client.goal_result = ThreadGoal(
        thread_id="lane-1",
        objective="ship",
        status="active",
        tokens_used=5,
        time_used_seconds=6,
        created_at=1,
        updated_at=2,
    )
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    got = await handlers.goal_get(GoalGetInput(lane="@alpha"), ctx)
    assert got.goal is not None
    assert got.goal.objective == "ship"

    set_out = await handlers.goal_set(
        GoalSetInput(lane="lane-1", objective="finish", token_budget=100),
        ctx,
    )
    assert set_out.goal is not None
    assert set_out.goal.objective == "finish"
    assert any(
        name == "thread_goal_set" and kw["objective"] == "finish" and kw["token_budget"] == 100
        for name, kw in client.calls
    )

    cleared = await handlers.goal_clear(GoalClearInput(lane="lane-1"), ctx)
    assert cleared.goal is None
    assert any(
        name == "thread_goal_clear" and kw["thread_id"] == "lane-1" for name, kw in client.calls
    )


async def test_goal_set_requires_a_change(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    with pytest.raises(ValidationError):
        await handlers.goal_set(GoalSetInput(lane="lane-1"), ctx)


async def test_goal_set_requires_objective_for_new_goal(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    with pytest.raises(ValidationError, match="requires objective"):
        await handlers.goal_set(GoalSetInput(lane="lane-1", status="complete"), ctx)

    assert any(name == "thread_goal_get" for name, _ in client.calls)
    assert not any(name == "thread_goal_set" for name, _ in client.calls)


async def test_goal_set_updates_existing_goal_without_objective(store: Registry) -> None:
    client = FakeLaneClient()
    client.goal_result = ThreadGoal(
        thread_id="lane-1",
        objective="ship",
        status="active",
        tokens_used=0,
        time_used_seconds=0,
        created_at=1,
        updated_at=2,
    )
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.goal_set(GoalSetInput(lane="lane-1", status="complete"), ctx)

    assert out.goal is not None
    assert out.goal.objective == "ship"
    assert out.goal.status == "complete"
    assert any(name == "thread_goal_get" for name, _ in client.calls)
    assert any(
        name == "thread_goal_set" and kw["status"] == "complete" and kw["objective"] is None
        for name, kw in client.calls
    )


async def test_fork_registers_new_owned_lane(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha", cwd="/source"), ctx)

    out = await handlers.fork(
        ForkInput(
            lane="@alpha",
            name="alpha-copy",
            cwd="/fork",
            sandbox="workspace-write",
            approval_policy="on-request",
            ephemeral=True,
        ),
        ctx,
    )

    assert out.id == "lane-1-fork"
    assert out.handle == "@alpha-copy"
    lane = await store.find_lane("lane-1-fork")
    assert lane is not None
    assert lane.source == "own"
    assert lane.cwd == "/fork"
    assert any(
        name == "thread_fork"
        and kw["thread_id"] == "lane-1"
        and kw["sandbox"] == "workspace-write"
        and kw["approval_policy"] == "on-request"
        for name, kw in client.calls
    )


async def test_rollback_and_compact_owned_lane(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)
    await store.set_active_turn("lane-1", "turn-9")
    await store.update_lane_status("lane-1", "busy")

    rolled = await handlers.rollback(RollbackInput(lane="lane-1", turns=2), ctx)
    assert rolled.status == "idle"
    assert any(
        name == "thread_rollback" and kw["thread_id"] == "lane-1" and kw["num_turns"] == 2
        for name, kw in client.calls
    )

    compacted = await handlers.compact(CompactInput(lane="@alpha"), ctx)
    assert compacted.op == "compact"
    assert any(
        name == "thread_compact_start" and kw["thread_id"] == "lane-1" for name, kw in client.calls
    )


async def test_history_control_ops_on_attached_lane_raise_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D6", handle="@desktop", source="attached", status="idle")

    with pytest.raises(AuthorityError):
        await handlers.goal_clear(GoalClearInput(lane="D6"), ctx)
    with pytest.raises(AuthorityError):
        await handlers.fork(ForkInput(lane="D6", name="copy"), ctx)
    with pytest.raises(AuthorityError):
        await handlers.rollback(RollbackInput(lane="D6"), ctx)
    with pytest.raises(AuthorityError):
        await handlers.compact(CompactInput(lane="D6"), ctx)


async def test_send_to_attached_lane_raises_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")
    with pytest.raises(AuthorityError) as exc:
        await handlers.send(LaneTextInput(lane="D1", text="nope"), ctx)
    assert "source=attached" in str(exc.value)
    assert "allow_attached_writes" in str(exc.value)


async def test_attached_lane_policy_allows_send_and_context_injection(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
    lane = await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")

    sent = await handlers.send(LaneTextInput(lane=lane.ref, text="hello"), ctx)
    assert sent.accepted is True
    assert sent.writable is True
    assert sent.capabilities.send is True
    assert sent.capabilities.context is True
    assert sent.write_locked_reason is None
    assert client.calls[0][0] == "thread_resume"
    assert any(name == "turn_start" and kw["thread_id"] == "D1" for name, kw in client.calls)

    injected = await handlers.brief(LaneTextInput(lane=lane.ref, text="context"), ctx)
    assert injected.op == "brief"
    assert [name for name, _ in client.calls].count("thread_resume") == 2
    assert any(name == "inject_items" and kw["thread_id"] == "D1" for name, kw in client.calls)


async def test_attached_lane_policy_allows_goal_set(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))
    lane = await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")

    out = await handlers.goal_set(GoalSetInput(lane=lane.ref, objective="check back in"), ctx)

    assert out.writable is True
    assert out.goal is not None
    assert out.goal.objective == "check back in"
    assert client.calls[0][0] == "thread_resume"
    assert any(name == "thread_goal_set" and kw["thread_id"] == "D1" for name, kw in client.calls)


async def test_roster_and_show_report_attached_write_capabilities(store: Registry) -> None:
    ctx = make_ctx(store)
    lane = await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")

    roster = await handlers.roster(RosterInput(), ctx)
    item = roster.lanes[0]
    assert item.ref == lane.ref
    assert item.writable is False
    assert item.capabilities.read is True
    assert item.capabilities.send is False
    assert item.capabilities.context is False
    assert item.capabilities.goal_set is False
    assert item.write_locked_reason is not None

    detail = await handlers.show(ShowInput(lane=lane.ref), ctx)
    assert detail.writable is False
    assert detail.capabilities.send is False
    assert detail.write_locked_reason == item.write_locked_reason


async def test_roster_reports_attached_writable_when_policy_allows(store: Registry) -> None:
    ctx = make_ctx(store, policy=RuntimePolicy(allow_attached_writes=True))
    await store.add_lane(id="D1", handle="@desktop", source="attached", status="idle")

    item = (await handlers.roster(RosterInput(), ctx)).lanes[0]

    assert item.writable is True
    assert item.capabilities.send is True
    assert item.capabilities.context is True
    assert item.capabilities.goal_set is True
    assert item.write_locked_reason is None


async def test_archive_attached_lane_updates_thread_and_registry(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await store.add_lane(id="D2", handle="@desktop", source="attached", status="idle")

    out = await handlers.archive(ThreadTargetInput(target="D2"), ctx)

    assert out.status == "archived"
    assert (await store.get_lane("D2")).status == "archived"
    assert any(name == "thread_archive" and kw["thread_id"] == "D2" for name, kw in client.calls)


async def test_steer_attached_lane_raises_authority(store: Registry) -> None:
    # The authority guard precedes the active-turn check: attached lanes do not accept
    # turn-writing operations.
    ctx = make_ctx(store)
    await store.add_lane(id="D3", handle="@desktop", source="attached", status="idle")
    await store.set_active_turn("D3", "turn-1")
    with pytest.raises(AuthorityError):
        await handlers.steer(LaneTextInput(lane="D3", text="nope"), ctx)


async def test_brief_attached_lane_raises_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D4", handle="@desktop", source="attached", status="idle")
    with pytest.raises(AuthorityError):
        await handlers.brief(LaneTextInput(lane="D4", text="nope"), ctx)


async def test_stop_attached_lane_raises_authority(store: Registry) -> None:
    ctx = make_ctx(store)
    await store.add_lane(id="D5", handle="@desktop", source="attached", status="idle")
    with pytest.raises(AuthorityError):
        await handlers.stop(LaneInput(lane="D5"), ctx)


async def test_steer_requires_active_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="g"), ctx)
    with pytest.raises(ValidationError):
        await handlers.steer(LaneTextInput(lane="lane-1", text="also mention X"), ctx)
    await store.set_active_turn("lane-1", "turn-7")
    ack = await handlers.steer(LaneTextInput(lane="lane-1", text="also mention X"), ctx)
    assert ack.op == "steer"
    assert any(
        name == "turn_steer" and kw["expected_turn_id"] == "turn-7" for name, kw in client.calls
    )


async def test_stop_requires_active_turn(store: Registry) -> None:
    # App Server turn/interrupt requires a turnId; an idle lane has none.
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="g"), ctx)
    with pytest.raises(ValidationError):
        await handlers.stop(LaneInput(lane="lane-1"), ctx)
    assert not any(name == "turn_interrupt" for name, _ in client.calls)
    await store.set_active_turn("lane-1", "turn-9")
    ack = await handlers.stop(LaneInput(lane="lane-1"), ctx)
    assert ack.op == "stop"
    assert any(name == "turn_interrupt" and kw["turn_id"] == "turn-9" for name, kw in client.calls)


async def test_interrupt_requires_active_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="g"), ctx)
    with pytest.raises(ValidationError):
        await handlers.interrupt(LaneInput(lane="lane-1"), ctx)
    assert not any(name == "turn_interrupt" for name, _ in client.calls)


async def test_interject_requires_active_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="g"), ctx)
    with pytest.raises(ValidationError):
        await handlers.send_message(SendInput(lane="lane-1", text="replace", mode="interject"), ctx)
    assert not any(name == "turn_interrupt" for name, _ in client.calls)
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_roster_then_archive_flips_status(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="one"), ctx)
    roster = await handlers.roster(RosterInput(), ctx)
    assert [lane.handle for lane in roster.lanes] == ["@one"]
    archived = await handlers.archive(ThreadTargetInput(target="lane-1"), ctx)
    assert archived.status == "archived"
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []
    everything = await handlers.roster(RosterInput(include_archived=True), ctx)
    assert len(everything.lanes) == 1


class _NoRolloutArchiveClient(FakeLaneClient):
    async def thread_archive(self, thread_id: str) -> None:
        self._record("thread_archive", thread_id=thread_id)
        raise ClientAppServerError(-32600, f"no rollout found for thread id {thread_id}")


async def test_archive_no_rollout_lane_marks_local_lane_archived(store: Registry) -> None:
    client = _NoRolloutArchiveClient()
    ctx = make_ctx(store, client)
    await handlers.new_lane(NewInput(name="smoke", ephemeral=True, send=False), ctx)

    archived = await handlers.archive(ThreadTargetInput(target="lane-1"), ctx)

    assert archived.status == "archived"
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []
    everything = await handlers.roster(RosterInput(include_archived=True), ctx)
    assert [lane.id for lane in everything.lanes] == ["lane-1"]
    assert any(name == "thread_archive" for name, _ in client.calls)


async def test_archive_unmanaged_thread_does_not_register_lane(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.archive(ThreadTargetInput(target="raw-thread"), ctx)

    assert out.id == "raw-thread"
    assert out.managed is False
    assert out.status == "archived"
    assert (await handlers.roster(RosterInput(include_archived=True), ctx)).lanes == []
    assert any(
        name == "thread_archive" and kw["thread_id"] == "raw-thread" for name, kw in client.calls
    )


async def test_restore_managed_lane_unarchives_without_starting_turn(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="one"), ctx)
    client.threads["lane-1"] = ThreadInfo(id="lane-1", status=ThreadStatus(type="idle"))
    await store.update_lane_status("lane-1", "archived")

    restored = await handlers.restore(ThreadTargetInput(target="@one"), ctx)

    assert restored.status == "idle"
    assert (await store.get_lane("lane-1")).status == "idle"
    assert any(
        name == "thread_unarchive" and kw["thread_id"] == "lane-1" for name, kw in client.calls
    )
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_restore_unmanaged_thread_does_not_register_or_start_turn(store: Registry) -> None:
    client = FakeLaneClient()
    client.threads["raw-thread"] = ThreadInfo(id="raw-thread", status=ThreadStatus(type="idle"))
    ctx = make_ctx(store, client)

    restored = await handlers.restore(ThreadTargetInput(target="raw-thread"), ctx)

    assert restored.id == "raw-thread"
    assert restored.managed is False
    assert restored.status == "idle"
    assert (await handlers.roster(RosterInput(include_archived=True), ctx)).lanes == []
    assert any(
        name == "thread_unarchive" and kw["thread_id"] == "raw-thread" for name, kw in client.calls
    )
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_status_and_log_reflect_activity(store: Registry) -> None:
    ctx = make_ctx(store)
    await handlers.open_lane(OpenInput(name="one"), ctx)
    await handlers.send(LaneTextInput(lane="lane-1", text="hi"), ctx)
    status = await handlers.status(StatusInput(), ctx)
    assert status.lanes == 1
    assert status.busy == 1
    log = await handlers.show_log(LogInput(limit=10), ctx)
    ops = [a.op for a in log.actions]
    assert "open" in ops
    assert "send" in ops


class _HistoryReadClient(FakeLaneClient):
    def __init__(self, results: dict[str, dict[str, object]]) -> None:
        super().__init__()
        self.results = results

    async def thread_read(self, thread_id: str, include_turns: bool = False) -> dict[str, object]:
        self._record("thread_read", thread_id=thread_id, include_turns=include_turns)
        return self.results[thread_id]


def _thread_history(*, tool: str, path: str = "src/app.py") -> dict[str, object]:
    return {
        "thread": {
            "id": "thread",
            "turns": [
                {
                    "id": "turn-1",
                    "createdAt": "2026-06-16T10:00:00Z",
                    "items": [
                        {"id": "msg-1", "type": "message", "role": "user", "text": "do it"},
                        {
                            "id": "tool-1",
                            "type": "tool_call",
                            "toolName": tool,
                            "text": f"{tool} touched {path}",
                            "path": path,
                        },
                    ],
                }
            ],
        }
    }


async def test_history_overview_filters_by_tool_and_changed_worktree(
    store: Registry, tmp_path: Path
) -> None:
    dirty_repo = _git_repo_for_worktree(tmp_path / "dirty")
    (dirty_repo / "README.md").write_text("changed\n")
    clean_repo = _git_repo_for_worktree(tmp_path / "clean")
    dirty = await store.add_lane(
        id="dirty-lane", handle="@dirty", source="own", cwd=str(dirty_repo)
    )
    clean = await store.add_lane(
        id="clean-lane", handle="@clean", source="own", cwd=str(clean_repo)
    )
    client = _HistoryReadClient(
        {
            dirty.id: _thread_history(tool="bash", path="README.md"),
            clean.id: _thread_history(tool="python", path="src/app.py"),
        }
    )
    ctx = make_ctx(store, client)

    out = await handlers.history(HistoryInput(has_tool="bash", changed=True), ctx)

    assert out.mode == "overview"
    assert [thread.id for thread in out.threads] == ["dirty-lane"]
    assert out.threads[0].worktree.dirty is True
    assert out.threads[0].worktree.changed_files == ["README.md"]
    assert out.threads[0].unique_tools == ["bash"]


async def test_history_summary_reports_worktree_changed_files(
    store: Registry, tmp_path: Path
) -> None:
    repo = _git_repo_for_worktree(tmp_path / "repo")
    (repo / "README.md").write_text("changed\n")
    lane = await store.add_lane(id="lane-1", handle="@lane", source="own", cwd=str(repo))
    ctx = make_ctx(store, _HistoryReadClient({lane.id: _thread_history(tool="bash")}))

    out = await handlers.history(HistoryInput(lane="@lane"), ctx)

    assert out.thread is not None
    assert out.thread.worktree.repo == str(repo)
    assert out.thread.worktree.dirty is True
    assert out.thread.worktree.changed_files_count == 1
    assert out.thread.worktree.changed_files == ["README.md"]


async def test_attach_is_idempotent(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    first = await handlers.attach_lane(AttachInput(thread="T9"), ctx)
    second = await handlers.attach_lane(AttachInput(thread="T9"), ctx)
    assert first.id == second.id == "T9"
    assert len((await handlers.roster(RosterInput(), ctx)).lanes) == 1
    assert any(name == "thread_read" for name, _ in client.calls)
    assert not any(name == "thread_resume" for name, _ in client.calls)
    sync = await store.get_lane_sync("T9")
    assert sync is not None
    assert sync.state == "metadata"
    actions = await store.recent_actions(limit=10)
    assert [action.op for action in actions] == ["attach"]


class _HangingReadClient(FakeLaneClient):
    """A client whose metadata read never returns — models a wedged app-server."""

    async def thread_read(self, thread_id: str, include_turns: bool = False) -> dict[str, object]:
        await asyncio.sleep(3600)  # cancelled by the handler's wait_for bound
        raise AssertionError("unreachable")  # pragma: no cover


async def test_attach_metadata_timeout_projects_cleanly_and_leaves_registry_empty(
    store: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_ATTACH_METADATA_TIMEOUT_S", 0.05)
    ctx = make_ctx(store, _HangingReadClient())
    with pytest.raises(AppServerError) as excinfo:
        await handlers.attach_lane(AttachInput(thread="STUCK"), ctx)
    assert "timed out" in str(excinfo.value)
    # The bounded failure must not half-register a lane.
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []


async def test_attach_invalid_metadata_projects_cleanly_and_leaves_registry_empty(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.read_result = {"data": []}
    ctx = make_ctx(store, client)

    with pytest.raises(AppServerError) as excinfo:
        await handlers.attach_lane(AttachInput(thread="BAD"), ctx)

    assert "invalid payload" in str(excinfo.value)
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []


async def test_attach_with_sync_indexes_jsonl_and_roster_reports_state(
    store: Registry, tmp_path: Path
) -> None:
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"session_meta","timestamp":"2026-06-05T10:00:00.000Z",'
                '"payload":{"id":"T9","cwd":"/work","source":"vscode",'
                '"thread_source":"user","model_provider":"openai"}}',
                '{"type":"turn_context","timestamp":"2026-06-05T10:00:01.000Z",'
                '"payload":{"model":"test-model","effort":"low"}}',
                '{"type":"event_msg","timestamp":"2026-06-05T10:00:02.000Z",'
                '"payload":{"type":"task_complete","turn_id":"turn-1"}}',
            ]
        )
        + "\n"
    )
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "T9",
            "name": "Desktop",
            "preview": "hello from desktop",
            "cwd": "/work",
            "source": "vscode",
            "path": str(path),
            "sessionId": "T9",
            "modelProvider": "openai",
            "serviceTier": "priority",
        }
    }
    ctx = make_ctx(store, client)

    attached = await handlers.attach_lane(AttachInput(thread="T9", sync=True), ctx)
    detail = await handlers.show(ShowInput(lane="T9"), ctx)
    roster = await handlers.roster(RosterInput(), ctx)

    assert attached.handle == "Desktop"
    assert detail.sync.state == "partial"
    assert detail.sync.latest_turn_id == "turn-1"
    assert detail.sync.source_size == path.stat().st_size
    assert detail.model.model == "test-model"
    assert detail.model.service_tier.resolved == "priority"
    assert detail.model.service_tier.source == "observed"
    assert roster.lanes[0].sync.state == "partial"
    assert roster.lanes[0].sync.latest_event_at == "2026-06-05T10:00:02.000Z"
    assert roster.lanes[0].model.model == "test-model"
    stored = await store.get_lane_model_settings("T9")
    assert stored is not None
    assert stored.model_provider == "openai"
    assert stored.model == "test-model"
    assert stored.reasoning_effort == "low"
    assert stored.resolved_service_tier == "priority"
    assert stored.service_tier_source == "observed"
    assert sum(1 for name, _ in client.calls if name == "thread_read") == 1
    assert not any(name == "thread_resume" for name, _ in client.calls)


async def test_lane_sync_can_full_scan_existing_lane(store: Registry, tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        '{"type":"session_meta","timestamp":"2026-06-05T10:00:00.000Z","payload":{"id":"T9"}}\n'
    )
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "T9",
            "path": str(path),
            "modelProvider": "openai",
            "model": "gpt-5.5",
            "reasoningEffort": "xhigh",
            "serviceTier": "priority",
        }
    }
    ctx = make_ctx(store, client)
    await store.add_lane(id="T9", handle="@desktop", source="attached")

    out = await handlers.sync_lane(LaneSyncInput(lane="@desktop", full=True), ctx)

    assert out.lane == "T9"
    assert out.sync.state == "complete"
    assert out.sync.transcript_partial is False
    assert out.model.model == "gpt-5.5"
    assert out.model.service_tier.resolved == "priority"
    assert out.model.service_tier.source == "observed"
    assert any(name == "thread_read" for name, _ in client.calls)


async def test_discover_lists_persisted_sessions_from_client(store: Registry) -> None:
    client = FakeLaneClient()
    client.list_result = [
        ThreadInfo(
            id="019e8a09",
            name="Desktop",
            preview="  multi\n  line   preview  ",
            cwd="/work",
            source="cli",
            ephemeral=False,
            status=ThreadStatus(type="idle"),
            model_provider="openai",
            model="gpt-5.5",
            reasoning_effort="xhigh",
            service_tier="priority",
        ),
        ThreadInfo(id="t2"),  # sparse row: only an id
    ]
    ctx = make_ctx(store, client)
    out = await handlers.discover(DiscoverInput(limit=10), ctx)

    assert [s.id for s in out.sessions] == ["019e8a09", "t2"]
    first = out.sessions[0]
    assert first.name == "Desktop"
    assert first.status == "idle"  # flattened from the status object
    assert first.preview == "multi line preview"  # whitespace collapsed
    assert first.cwd == "/work"
    assert first.source == "cli"
    assert first.ephemeral is False
    assert first.model.model == "gpt-5.5"
    assert first.model.service_tier.resolved == "priority"
    assert first.model.service_tier.source == "observed"
    # Discovery reads through to the client's thread_list with the requested limit
    # AND state-db only — the latter is what keeps it read-only (no live resume).
    assert any(
        name == "thread_list"
        and kw["limit"] == 10
        and kw["archived"] is False
        and kw["sort_direction"] == "desc"
        and kw["sort_key"] == "updated_at"
        and kw["use_state_db_only"] is True
        for name, kw in client.calls
    )
    # ...and registers nothing (pure read; lane authority untouched).
    assert (await handlers.roster(RosterInput(), ctx)).lanes == []


async def test_discover_shortens_long_preview(store: Registry) -> None:
    client = FakeLaneClient()
    client.list_result = [ThreadInfo(id="t1", preview="x" * 200)]
    ctx = make_ctx(store, client)
    out = await handlers.discover(DiscoverInput(), ctx)
    preview = out.sessions[0].preview
    assert preview is not None
    assert len(preview) <= 80
    assert preview.endswith("…")


async def test_discover_keeps_short_preview_verbatim(store: Registry) -> None:
    exactly_80 = "y" * 80
    client = FakeLaneClient()
    client.list_result = [ThreadInfo(id="t1", preview=exactly_80)]
    ctx = make_ctx(store, client)
    out = await handlers.discover(DiscoverInput(), ctx)
    # At the boundary the preview is returned unchanged — no ellipsis.
    assert out.sessions[0].preview == exactly_80


async def test_search_uses_app_server_and_filters_managed_state_and_repo(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    client = FakeLaneClient()
    client.search_result = ThreadSearchResult(
        data=[
            ThreadSearchMatch(
                snippet="needle in managed",
                thread=ThreadInfo(
                    id="M1",
                    name="Managed",
                    cwd=str(repo / "subdir"),
                    created_at=100_000,
                    updated_at=200_000,
                    preview="managed preview",
                    status=ThreadStatus(type="idle"),
                ),
            ),
            ThreadSearchMatch(
                snippet="needle in unmanaged",
                thread=ThreadInfo(
                    id="U1",
                    name="Unmanaged",
                    cwd=str(outside),
                    created_at=100_000,
                    updated_at=200_000,
                    status=ThreadStatus(type="idle"),
                ),
            ),
        ]
    )
    ctx = make_ctx(store, client)
    await store.add_lane(id="M1", handle="@managed", source="attached", status="idle")

    out = await handlers.search(
        SearchInput(query="needle", managed=True, repo=str(repo), since="1970-01-01"),
        ctx,
    )

    assert [match.id for match in out.matches] == ["M1"]
    assert out.matches[0].handle == "@managed"
    assert out.matches[0].managed is True
    assert out.scanned == 2
    assert any(
        name == "thread_search" and kw["search_term"] == "needle" and kw["sort_key"] == "updated_at"
        for name, kw in client.calls
    )


async def test_search_can_filter_unmanaged_threads(store: Registry) -> None:
    client = FakeLaneClient()
    client.search_result = ThreadSearchResult(
        data=[
            ThreadSearchMatch(snippet="needle", thread=ThreadInfo(id="managed")),
            ThreadSearchMatch(snippet="needle", thread=ThreadInfo(id="raw")),
        ]
    )
    ctx = make_ctx(store, client)
    await store.add_lane(id="managed", handle="@managed", source="attached", status="idle")

    out = await handlers.search(SearchInput(query="needle", unmanaged=True), ctx)

    assert [match.id for match in out.matches] == ["raw"]
    assert out.matches[0].source == "unmanaged"


async def test_lane_search_reads_one_thread_transcript(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "name": "Docs",
            "cwd": "/work",
            "updatedAt": 200,
            "turns": [
                {
                    "id": "t1",
                    "items": [
                        {"id": "a1", "type": "agentMessage", "text": "nothing here"},
                        {"id": "a2", "type": "agentMessage", "text": "needle appears"},
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="docs"), ctx)

    out = await handlers.search(SearchInput(query="needle", lane="@docs"), ctx)

    assert [match.snippet for match in out.matches] == ["needle appears"]
    assert out.matches[0].handle == "@docs"
    assert out.scanned == 2
    assert any(
        name == "thread_read" and kw["thread_id"] == "lane-1" and kw["include_turns"] is True
        for name, kw in client.calls
    )
    assert not any(name == "thread_search" for name, _ in client.calls)


async def test_search_rejects_conflicting_managed_filters(store: Registry) -> None:
    ctx = make_ctx(store)
    with pytest.raises(ValidationError):
        await handlers.search(SearchInput(query="needle", managed=True, unmanaged=True), ctx)


def _write_packet(root: Path) -> Path:
    pkt = root / "packet"
    pkt.mkdir()
    (pkt / "goal.md").write_text("Packet goal.")
    (pkt / "prompt.md").write_text("Packet prompt.")
    (pkt / "output.schema.json").write_text('{"type": "object"}')
    return pkt


async def test_plan_new_lane_makes_no_mutation(store: Registry, tmp_path: Path) -> None:
    pkt = _write_packet(tmp_path)
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    plan = await handlers.plan_new_lane(
        NewInput(name="preview", cwd=str(tmp_path), packet=str(pkt)), ctx
    )

    assert plan.goal_set is True
    assert plan.would_send is True
    assert plan.output_schema_present is True
    assert client.calls == []  # no thread_start / goal_set / turn_start
    assert (await store.find_lane("lane-1")) is None
    slots = {src.slot for src in plan.sources}
    assert {"goal", "prompt", "output_schema"} <= slots


async def test_new_lane_launches_from_packet(store: Registry, tmp_path: Path) -> None:
    pkt = _write_packet(tmp_path)
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(NewInput(name="worker", cwd=str(tmp_path), packet=str(pkt)), ctx)

    assert out.goal_set is True
    assert out.message_accepted is True
    assert any(
        name == "thread_goal_set" and kw["objective"] == "Packet goal." for name, kw in client.calls
    )
    assert any(
        name == "turn_start"
        and kw["text"] == "Packet prompt."
        and kw["output_schema"] == {"type": "object"}
        for name, kw in client.calls
    )


async def test_new_lane_rejects_invalid_schema_file_before_thread_start(
    store: Registry, tmp_path: Path
) -> None:
    schema_file = tmp_path / "bad.json"
    schema_file.write_text("{not valid json")
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(ValidationError):
        await handlers.new_lane(
            NewInput(
                name="worker",
                cwd=str(tmp_path),
                text="hi",
                output_schema_file=str(schema_file),
            ),
            ctx,
        )

    assert client.calls == []
    assert (await store.find_lane("lane-1")) is None


def _stage_packet(root: Path) -> Path:
    pkt = root / "packet"
    pkt.mkdir()
    (pkt / "goal.md").write_text("Stage goal.")
    (pkt / "prompt.md").write_text("Stage prompt.")
    return pkt


async def test_new_lane_stages_packet_parts(store: Registry, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    pkt = _stage_packet(tmp_path)
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(name="worker", cwd=str(cwd), packet=str(pkt), stage="all"), ctx
    )

    assert set(out.staged.parts) == {"goal", "prompt"}
    assert out.staged.session_dir is not None
    session = Path(out.staged.session_dir)
    assert session == cwd / ".agents" / "sessions" / out.ref
    assert (session / "packet" / "goal.md").read_text() == "Stage goal."
    assert (session / "state.json").is_file()
    # Staging happens before the first turn, which still runs.
    assert any(name == "turn_start" for name, _ in client.calls)


async def test_new_lane_staging_failure_prevents_turn(store: Registry, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    (cwd / ".agents").mkdir(parents=True)
    (cwd / ".agents" / "sessions").write_text("not a directory")  # makes staging fail
    pkt = _stage_packet(tmp_path)
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(StagingError):
        await handlers.new_lane(
            NewInput(name="worker", cwd=str(cwd), packet=str(pkt), stage="goal", text="hi"), ctx
        )

    # Lane stays registered, marked error; the first turn never started.
    lane = await store.find_lane("lane-1")
    assert lane is not None
    assert lane.status == "error"
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_plan_new_lane_reports_stage_without_writing(store: Registry, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    pkt = _stage_packet(tmp_path)
    ctx = make_ctx(store)

    plan = await handlers.plan_new_lane(
        NewInput(name="worker", cwd=str(cwd), packet=str(pkt), stage="all"), ctx
    )

    assert set(plan.stage.parts) == {"goal", "prompt"}
    assert plan.stage.session_dir is None  # dry-run: ref unknown, nothing written
    assert not (cwd / ".agents").exists()


async def test_plan_new_lane_reports_workspace_without_setup(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text(
        """
version = 1
name = "repo"

[setup]
script = "touch SHOULD_NOT_EXIST"
"""
    )
    ctx = make_ctx(store)

    plan = await handlers.plan_new_lane(
        NewInput(name="worker", cwd=str(repo), workspace="auto"), ctx
    )

    assert plan.cwd == str(repo)
    assert plan.workspace.state == "discovered"
    assert plan.workspace.environment is not None
    assert plan.workspace.environment.name == "repo"
    assert plan.workspace.setup.ran is False
    assert not (repo / "SHOULD_NOT_EXIST").exists()


async def test_new_lane_workspace_auto_uses_effective_repo_cwd(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    nested = repo / "nested"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text('version = 1\nname = "repo"\n')
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(name="worker", cwd=str(nested), workspace="auto", send=False), ctx
    )

    assert out.cwd == str(repo)
    assert out.workspace.effective_cwd == str(repo)
    assert out.workspace.repo_root == str(repo)
    assert any(name == "thread_start" and kw["cwd"] == str(repo) for name, kw in client.calls)


async def test_new_lane_workspace_setup_requires_policy_or_explicit_run(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text(
        """
version = 1
name = "repo"

[setup]
script = "printf ran > setup.txt"
"""
    )
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(name="worker", cwd=str(repo), workspace="auto", send=False), ctx
    )

    assert out.workspace.setup.ran is False
    assert out.workspace.setup.policy == "not_allowed"
    assert not (repo / "setup.txt").exists()


async def test_new_lane_workspace_setup_runs_with_explicit_run(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text(
        """
version = 1
name = "repo"

[setup]
script = "printf ran > setup.txt"
"""
    )
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="worker",
            cwd=str(repo),
            workspace="auto",
            workspace_setup="run",
            send=False,
        ),
        ctx,
    )

    assert out.workspace.state == "setup_completed"
    assert out.workspace.setup.ran is True
    assert (repo / "setup.txt").read_text() == "ran"


async def test_new_lane_workspace_setup_failure_prevents_thread_start(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    env_dir = repo / ".codex" / "environments"
    env_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (env_dir / "environment.toml").write_text(
        """
version = 1
name = "repo"

[setup]
script = "exit 4"
"""
    )
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(ValidationError, match="workspace setup failed"):
        await handlers.new_lane(
            NewInput(
                name="worker",
                cwd=str(repo),
                workspace="auto",
                workspace_setup="run",
                send=False,
            ),
            ctx,
        )

    assert not any(name == "thread_start" for name, _ in client.calls)


async def test_new_lane_dispatch_created_worktree_is_effective_cwd_for_stage_and_thread(
    store: Registry, tmp_path: Path
) -> None:
    repo = _git_repo_for_worktree(tmp_path / "repo")
    pkt = _stage_packet(tmp_path)
    worktree_path = tmp_path / "worker-wt"
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="worker",
            cwd=str(repo),
            packet=str(pkt),
            stage="all",
            send=False,
            worktree="create",
            worktree_path=str(worktree_path),
            worktree_branch="dispatch/worker",
        ),
        ctx,
    )

    assert out.cwd == str(worktree_path)
    assert out.workspace.worktree.state == "created"
    assert out.workspace.worktree.created is True
    assert out.workspace.worktree.branch == "dispatch/worker"
    assert any(
        name == "thread_start" and kw["cwd"] == str(worktree_path) for name, kw in client.calls
    )
    assert out.staged.session_dir == str(worktree_path / ".agents" / "sessions" / out.ref)
    assert (worktree_path / ".agents" / "sessions" / out.ref / "packet" / "goal.md").is_file()


def _git_repo_for_worktree(path: Path) -> Path:
    path.mkdir()
    _run_git_for_worktree(path, "init", "-q")
    _run_git_for_worktree(path, "config", "user.email", "dispatch@example.test")
    _run_git_for_worktree(path, "config", "user.name", "Dispatch Test")
    (path / "README.md").write_text("hi\n")
    _run_git_for_worktree(path, "add", "README.md")
    _run_git_for_worktree(path, "commit", "-qm", "init")
    return path


def _run_git_for_worktree(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout.strip()
