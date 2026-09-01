"""Stateful handler tests (the cases examples can't reach from a fresh ctx)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError as PydanticValidationError

from outfitter.dispatch.client.errors import AppServerError as ClientAppServerError
from outfitter.dispatch.client.errors import TransportError
from outfitter.dispatch.client.events import LaneIdle, TurnFailed, TurnStarted
from outfitter.dispatch.client.models import (
    AppModel,
    ApprovalPolicy,
    ApprovalsReviewer,
    ConfigInfo,
    Effort,
    PermissionProfileSummary,
    Personality,
    ReasoningSummary,
    SandboxPolicy,
    ThreadGoal,
    ThreadInfo,
    ThreadItemsPage,
    ThreadResumeResult,
    ThreadSearchMatch,
    ThreadSearchResult,
    ThreadStatus,
    ThreadTurn,
    ThreadTurnsPage,
    UserInput,
)
from outfitter.dispatch.config import CapturePolicy, RuntimePolicy
from outfitter.dispatch.contracts.errors import (
    AppServerError,
    AuthorityError,
    NotFoundError,
    StagingError,
    ValidationError,
)
from outfitter.dispatch.core import handlers, queue
from outfitter.dispatch.core.models import (
    AttachInput,
    CompactInput,
    DiscoverInput,
    ForkInput,
    GoalClearInput,
    GoalGetInput,
    GoalSetInput,
    HistoryInput,
    ImageUrlContent,
    InboxAckInput,
    InboxListInput,
    InboxReadInput,
    LaneInput,
    LaneRenameInput,
    LaneSyncInput,
    LaneTextInput,
    LocalImageContent,
    LogInput,
    MessageContent,
    ModelsInput,
    NewInput,
    OpenInput,
    PermissionProfilesInput,
    QueryInput,
    RollbackInput,
    RosterInput,
    SearchInput,
    SendInput,
    ShowInput,
    StatusInput,
    SubscribeInput,
    SubscriptionListInput,
    TextContent,
    ThreadTargetInput,
    TranscriptInput,
    WatchInput,
)
from outfitter.dispatch.core.reactor import Reactor
from outfitter.dispatch.core.triggers import TriggerRunner
from outfitter.dispatch.registry.store import Registry
from tests.fakes import FakeLaneClient, make_ctx
from tests.fixtures import load_json
from tests.fixtures.registry.builders import thread_item, thread_item_ref, thread_turn


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
    receipts = await store.list_message_receipts(lane="lane-1")
    assert len(receipts) == 1
    assert receipts[0].status == "sent"


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


async def test_new_lane_rejects_config_selected_claude_provider_before_any_mutation(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "dispatch"
    (repo / ".dispatch").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".dispatch" / "config.toml").write_text('[defaults]\nprovider = "claude"\n')
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    with pytest.raises(ValidationError, match="not launchable"):
        await handlers.new_lane(NewInput(name="blocked", cwd=str(repo), send=False), ctx)

    assert client.calls == []


async def test_new_lane_cli_codex_overrides_config_claude_provider(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "dispatch"
    (repo / ".dispatch").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".dispatch" / "config.toml").write_text('[defaults]\nprovider = "claude"\n')
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(name="builder", cwd=str(repo), provider="codex", send=False), ctx
    )

    assert out.status == "idle"
    assert any(name == "thread_start" for name, _ in client.calls)


async def test_new_lane_validates_and_persists_permission_profile(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="profiled",
            cwd=str(tmp_path),
            text="start",
            permission_profile=":workspace",
        ),
        ctx,
    )

    assert any(
        name == "permission_profile_list" and kw["cwd"] == str(tmp_path)
        for name, kw in client.calls
    )
    assert any(
        name == "thread_start" and kw["permission_profile"] == ":workspace"
        for name, kw in client.calls
    )
    assert any(
        name == "turn_start" and kw["permission_profile"] == ":workspace"
        for name, kw in client.calls
    )
    stored = await store.get_lane_runtime_settings(out.id)
    assert stored is not None and stored.permission_profile == ":workspace"
    await store.update_lane_status(out.id, "idle")
    await handlers.send(LaneTextInput(lane=out.ref, text="continue"), ctx)
    assert client.calls[-1][0] == "turn_start"
    assert client.calls[-1][1]["permission_profile"] == ":workspace"


async def test_new_lane_rejects_unknown_or_disallowed_permission_profile(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    client.permission_profiles_result = [
        PermissionProfileSummary(id=":read-only", allowed=True),
        PermissionProfileSummary(id=":workspace", allowed=False),
    ]
    ctx = make_ctx(store, client)

    with pytest.raises(ValidationError, match=r"not allowed.*:read-only"):
        await handlers.new_lane(
            NewInput(name="blocked", cwd=str(tmp_path), permission_profile=":workspace"), ctx
        )
    with pytest.raises(ValidationError, match=r"unknown.*:read-only"):
        await handlers.new_lane(
            NewInput(name="missing", cwd=str(tmp_path), permission_profile=":missing"), ctx
        )


async def test_permissions_reports_live_and_older_binary_states(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    out = await handlers.permission_profiles(
        PermissionProfilesInput(cwd=str(tmp_path)), make_ctx(store, client)
    )
    assert out.catalog_state == "ready"
    assert out.source == "app-server"
    assert [profile.id for profile in out.profiles] == [
        ":read-only",
        ":workspace",
        ":danger-full-access",
    ]

    class OlderClient(FakeLaneClient):
        async def permission_profile_list(
            self, *, cwd: str | None = None, limit: int | None = None
        ) -> list[PermissionProfileSummary]:
            raise ClientAppServerError(-32601, "method not found")

    unsupported = await handlers.permission_profiles(
        PermissionProfilesInput(cwd=str(tmp_path)), make_ctx(store, OlderClient())
    )
    assert unsupported.catalog_state == "unsupported"
    assert unsupported.source == "registry"
    with pytest.raises(ValidationError, match="not supported"):
        await handlers.new_lane(
            NewInput(name="old", cwd=str(tmp_path), permission_profile=":read-only"),
            make_ctx(store, OlderClient()),
        )

    disallowed = FakeLaneClient()
    disallowed.permission_profiles_result = [PermissionProfileSummary(id=":blocked", allowed=False)]
    filtered = await handlers.permission_profiles(
        PermissionProfilesInput(cwd=str(tmp_path)), make_ctx(store, disallowed)
    )
    assert filtered.catalog_state == "ready"
    assert filtered.refreshed_at is not None
    assert filtered.profiles == []


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


async def test_new_lane_accepts_model_defined_reasoning_effort(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    client.models_result.append(
        AppModel(
            id="gpt-5.6-sol",
            supported_reasoning_efforts=["low", "max", "ultra"],
        )
    )
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="sol",
            cwd=str(tmp_path),
            model="gpt-5.6-sol",
            effort="ultra",
            send=False,
        ),
        ctx,
    )

    assert out.model.reasoning_effort == "ultra"
    call = next(kw for name, kw in client.calls if name == "thread_start")
    assert call["model"] == "gpt-5.6-sol"


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
    assert refreshed.models[0].input_modalities == ["text", "image"]
    assert refreshed.models[0].supports_personality is True
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


def test_new_input_rejects_conflicting_permission_settings_at_boundary() -> None:
    with pytest.raises(PydanticValidationError, match="permission_profile cannot be combined"):
        NewInput(
            name="conflict",
            permission_profile=":workspace",
            sandbox="read-only",
        )


async def test_image_launch_uses_unique_catalog_default_when_config_omits_model(
    store: Registry, tmp_path: Path
) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")
    client = FakeLaneClient()
    client.config_result = ConfigInfo(model=None, model_provider="openai")
    client.models_result = [
        AppModel(id="default-image", is_default=True, input_modalities=["text", "image"])
    ]
    ctx = make_ctx(store, client)

    out = await handlers.new_lane(
        NewInput(
            name="default-image",
            cwd=str(tmp_path),
            content=[LocalImageContent(path=str(image))],
        ),
        ctx,
    )

    assert out.model.model == "default-image"


async def test_image_launch_reports_image_capable_model_choices(
    store: Registry, tmp_path: Path
) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")
    client = FakeLaneClient()
    client.models_result = [
        AppModel(id="text-only", input_modalities=["text"]),
        AppModel(id="vision", input_modalities=["text", "image"]),
    ]
    ctx = make_ctx(store, client)

    with pytest.raises(ValidationError, match="models advertising them: vision"):
        await handlers.new_lane(
            NewInput(
                name="bad-model",
                cwd=str(tmp_path),
                model="text-only",
                content=[LocalImageContent(path=str(image))],
            ),
            ctx,
        )

    assert not any(name == "thread_start" for name, _ in client.calls)


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
        input_items: list[UserInput] | None = None,
        permission_profile: str | None = None,
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
            input_items=input_items,
            permission_profile=permission_profile,
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


async def test_context_preserves_structured_text_content(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="beta"), ctx)

    await handlers.send_message(
        SendInput(
            lane="@beta",
            text="first",
            content=[TextContent(text="second")],
            mode="context",
        ),
        ctx,
    )

    call = next(kw for name, kw in client.calls if name == "inject_items")
    assert call["items"] == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "first"},
                {"type": "input_text", "text": "second"},
            ],
        }
    ]


def test_context_rejects_image_content_before_handler() -> None:
    with pytest.raises(PydanticValidationError, match="not supported in context mode"):
        SendInput(
            lane="@beta",
            mode="context",
            content=[ImageUrlContent(url="https://example.com/a.png")],
        )


async def test_steer_and_interject_project_local_image_input(
    store: Registry, tmp_path: Path
) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="images", cwd=str(tmp_path)), ctx)
    await store.set_active_turn("lane-1", "turn-1")
    content: list[MessageContent] = [LocalImageContent(path=str(image), detail="high")]

    await handlers.send_message(
        SendInput(lane="@images", text="steer", content=content, mode="steer"), ctx
    )
    await handlers.send_message(
        SendInput(lane="@images", text="replace", content=content, mode="interject"), ctx
    )

    steer_call = next(kw for name, kw in client.calls if name == "turn_steer")
    assert steer_call["input_items"] == [
        {"type": "localImage", "path": str(image), "detail": "high"}
    ]
    interject_call = [kw for name, kw in client.calls if name == "turn_start"][-1]
    assert interject_call["input_items"] == [
        {"type": "localImage", "path": str(image), "detail": "high"}
    ]


async def test_send_intro_appends_managed_sender_from_codex_thread_id(
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
        "hello\n\n"
        f"dispatch (dm): [@Dispatch](codex://threads/{sender.id}) `{sender.ref}`\n"
        f'↳ reply `dispatch send {sender.ref} "..."`'
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
    assert queued.text.startswith("later\n\ndispatch (dm): ")
    assert f"[@Dispatch](codex://threads/{sender.id})" in queued.text
    assert f"`{sender.ref}`" in queued.text
    assert queued.text.endswith(f'↳ reply `dispatch send {sender.ref} "..."`')


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
    receipts = await store.list_message_receipts(lane="lane-1")
    assert len(receipts) == 1
    assert receipts[0].queued_message_id == queued.id
    assert receipts[0].status == "created"


async def test_queued_local_image_stores_metadata_not_bytes(
    store: Registry, tmp_path: Path
) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"secret-image-payload")
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="images", cwd=str(tmp_path)), ctx)
    await store.update_lane_status("lane-1", "busy")

    await handlers.send_message(
        SendInput(
            lane="@images",
            mode="queue",
            content=[LocalImageContent(path=str(image))],
        ),
        ctx,
    )

    queued = await store.get_queued_message(1)
    encoded = json.dumps(queued.content)
    assert "secret-image-payload" not in encoded
    assert queued.content[0]["media_type"] == "image/png"
    assert queued.content[0]["size"] == len(image.read_bytes())
    assert len(str(queued.content[0]["sha256"])) == 64


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
    receipts = await store.list_message_receipts(lane="lane-1")
    assert len(receipts) == 1
    assert receipts[0].queued_message_id == sent.id
    assert receipts[0].status == "sent"
    assert any(name == "turn_start" and kw["text"] == "now" for name, kw in client.calls)


async def test_send_mixed_images_reaches_turn_start(store: Registry, tmp_path: Path) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="images", cwd=str(tmp_path)), ctx)

    ack = await handlers.send_message(
        SendInput(
            lane="@images",
            text="inspect",
            content=[
                LocalImageContent(path="sample.png", detail="high"),
            ],
        ),
        ctx,
    )

    assert ack.op == "send"
    call = next(kw for name, kw in client.calls if name == "turn_start")
    assert call["text"] == "inspect"
    assert call["input_items"] == [
        {"type": "localImage", "path": str(image), "detail": "high"},
    ]
    assert any(name == "model_list" for name, _ in client.calls)


async def test_queued_missing_image_fails_without_starting_turn(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="images", cwd=str(tmp_path)), ctx)
    await store.update_lane_status("lane-1", "busy")
    await handlers.send_message(
        SendInput(
            lane="@images",
            mode="queue",
            content=[LocalImageContent(path="missing.png")],
        ),
        ctx,
    )
    await store.update_lane_status("lane-1", "idle")
    before = len([1 for name, _ in client.calls if name == "turn_start"])

    assert await queue.drain_next_queued_message(ctx, "lane-1") is True
    queued = await store.get_queued_message(1)
    assert queued.status == "error"
    assert len([1 for name, _ in client.calls if name == "turn_start"]) == before


async def test_idle_queue_surfaces_immediate_missing_image_failure(
    store: Registry, tmp_path: Path
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="images", cwd=str(tmp_path)), ctx)

    with pytest.raises(ValidationError, match="failed during immediate delivery"):
        await handlers.send_message(
            SendInput(
                lane="@images",
                mode="queue",
                content=[LocalImageContent(path="missing.png")],
            ),
            ctx,
        )

    queued = await store.get_queued_message(1)
    assert queued.status == "error"
    assert "local image not found" in (queued.error or "")


async def test_queue_audit_redacts_signed_url_and_preserves_text_summary(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="images"), ctx)
    await store.update_lane_status("lane-1", "busy")

    await handlers.send_message(
        SendInput(
            lane="@images",
            text="token=supersecret inspect",
            mode="queue",
            content=[ImageUrlContent(url="https://example.com/a.png?token=signed-secret")],
        ),
        ctx,
    )

    actions = await store.recent_actions()
    detail = next(action.detail for action in actions if action.op == "queue") or ""
    assert "supersecret" not in detail
    assert "signed-secret" not in detail
    assert "[redacted]" in detail
    assert "https://example.com/a.png" in detail


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
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha", cwd="/tmp/no-such-history-worktree"), ctx)
    await store.upsert_thread_turn(
        thread_turn(lane="lane-1", provider_thread_id="lane-1", turn_id="t1")
    )
    await store.upsert_thread_item(
        thread_item(
            lane="lane-1", provider_thread_id="lane-1", turn_id="t1", item_id="u1"
        ).model_copy(
            update={
                "item_type": "userMessage",
                "role": "user",
                "text": "run status",
                "tool": None,
            }
        )
    )
    await store.upsert_thread_item(
        thread_item(lane="lane-1", provider_thread_id="lane-1", turn_id="t1", item_id="tool-1"),
        refs=[thread_item_ref(provider_thread_id="lane-1", item_id="tool-1", ref_value="bash")],
    )
    await store.upsert_thread_item(
        thread_item(
            lane="lane-1",
            provider_thread_id="lane-1",
            turn_id="t1",
            item_id="file-1",
            position=2,
        ).model_copy(update={"item_type": "fileChange", "text": "edited app", "tool": None}),
        refs=[
            thread_item_ref(
                provider_thread_id="lane-1",
                item_id="file-1",
                ref_type="file",
                ref_value="src/app.py",
            )
        ],
    )

    out = await handlers.history(HistoryInput(), ctx)

    assert not any(name == "thread_read" for name, _ in client.calls)
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
    await store.upsert_thread_turn(
        thread_turn(
            lane="lane-1",
            provider_thread_id="lane-1",
            turn_id="t1",
            status="completed",
            updated_at="2026-06-03T12:00:00+00:00",
        )
    )

    summary = await handlers.history(HistoryInput(lane="@alpha"), ctx)
    tools = await handlers.history(HistoryInput(lane="@alpha", view="tools"), ctx)
    files = await handlers.history(HistoryInput(lane="@alpha", view="files"), ctx)
    indexed_items_view = await handlers.history(
        HistoryInput(lane="@alpha", view="items", tool="bash"), ctx
    )
    items = await handlers.history(
        HistoryInput(lane="@alpha", view="items", tool="bash", raw=True), ctx
    )

    assert summary.mode == "summary"
    assert summary.thread is not None
    assert summary.thread.tool_calls == 2
    assert [tool.tool for tool in tools.tools] == ["bash", "apply_patch"]
    assert files.files[0].path == "src/app.py"
    assert len(indexed_items_view.items) == 1
    assert indexed_items_view.items[0].tool == "bash"
    assert indexed_items_view.items[0].raw is None
    assert len(items.items) == 1
    assert items.items[0].tool == "bash"
    assert items.items[0].raw is not None
    indexed_turns = await store.list_thread_turns(lane="lane-1")
    assert len(indexed_turns) == 1
    assert indexed_turns[0].turn_id == "t1"
    assert indexed_turns[0].status == "completed"
    assert indexed_turns[0].completion_source is None
    indexed_items = await store.list_thread_items(lane="lane-1", turn_id="t1")
    assert [item.item_id for item in reversed(indexed_items)] == ["a1", "b1", "p1"]
    assert all(item.payload is None for item in indexed_items)
    assert all(item.raw_retained is False for item in indexed_items)
    refs = await store.list_thread_item_refs(await store.get_thread_item("codex", "lane-1", "p1"))
    assert [(ref.ref_type, ref.ref_value) for ref in refs] == [
        ("file", "src/app.py"),
        ("tool", "apply_patch"),
    ]


async def test_history_index_honors_error_only_raw_retention(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "status": "failed",
                    "error": "failed because provider exploded",
                    "items": [
                        {
                            "id": "err1",
                            "type": "toolError",
                            "toolName": "bash",
                            "text": "command failed",
                            "error": "exit 1",
                        }
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client, capture=CapturePolicy(raw_payload_retention="errors"))
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    await handlers.history(HistoryInput(lane="@alpha"), ctx)

    [turn] = await store.list_thread_turns(lane="lane-1")
    assert turn.error == "failed because provider exploded"
    [item] = await store.list_thread_items(lane="lane-1", turn_id="t1")
    assert item.text == "command failed"
    assert item.raw_retained is True
    assert item.payload is not None
    assert item.payload["error"] == "exit 1"


@pytest.mark.parametrize(
    ("capture", "expected_retained"),
    [
        (CapturePolicy(mode="debug", raw_payload_retention="debug", max_payload_bytes=80), True),
        (CapturePolicy(raw_payload_retention="all", max_payload_bytes=80), True),
        (
            CapturePolicy(mode="standard", raw_payload_retention="debug", max_payload_bytes=80),
            False,
        ),
    ],
)
async def test_history_index_bounds_debug_and_all_raw_retention(
    store: Registry, capture: CapturePolicy, expected_retained: bool
) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [
                        {
                            "id": "m1",
                            "type": "agentMessage",
                            "text": "visible",
                            "metadata": {"blob": "x" * 400},
                        }
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client, capture=capture)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    await handlers.history(HistoryInput(lane="@alpha"), ctx)

    [item] = await store.list_thread_items(lane="lane-1", turn_id="t1")
    assert item.raw_retained is expected_retained
    if expected_retained:
        assert item.payload is not None
        assert item.payload["truncated"] is True
        retained_bytes = len(json.dumps(item.payload, separators=(",", ":")).encode("utf-8"))
        assert retained_bytes <= capture.max_payload_bytes
    else:
        assert item.payload is None


@pytest.mark.parametrize("entrypoint", ["history", "transcript", "show"])
async def test_thread_read_entrypoints_use_ctx_capture_policy(
    store: Registry, entrypoint: str
) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [{"id": "m1", "type": "agentMessage", "text": "abcdef"}],
                }
            ],
        }
    }
    ctx = make_ctx(store, client, capture=CapturePolicy(max_text_bytes=4))
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    if entrypoint == "history":
        await handlers.history(HistoryInput(lane="@alpha"), ctx)
    elif entrypoint == "transcript":
        await handlers.transcript(TranscriptInput(lane="@alpha"), ctx)
    else:
        await handlers.show(ShowInput(lane="@alpha", include_transcript=True), ctx)

    [item] = await store.list_thread_items(lane="lane-1", turn_id="t1")
    assert item.text == "abcd"
    assert item.payload is None
    assert item.raw_retained is False


async def test_history_index_minimal_capture_skips_thread_items(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [
                        {
                            "id": "m1",
                            "type": "agentMessage",
                            "text": "do not persist searchable text",
                        }
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client, capture=CapturePolicy(mode="minimal"))
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    await handlers.history(HistoryInput(lane="@alpha"), ctx)

    [turn] = await store.list_thread_turns(lane="lane-1")
    assert turn.turn_id == "t1"
    assert await store.list_thread_items(lane="lane-1", turn_id="t1") == []


async def test_history_index_bounds_searchable_thread_text(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [{"id": "m1", "type": "agentMessage", "text": "abcdef"}],
                }
            ],
        }
    }
    ctx = make_ctx(store, client, capture=CapturePolicy(max_text_bytes=4))
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    out = await handlers.history(HistoryInput(lane="@alpha", view="items"), ctx)

    [item] = await store.list_thread_items(lane="lane-1", turn_id="t1")
    assert out.items[0].text == "abcd"
    assert item.text == "abcd"
    assert item.payload is None
    assert item.raw_retained is False


async def test_history_indexed_views_do_not_truncate_before_filtering(
    store: Registry,
) -> None:
    item_count = 1205
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [
                        {
                            "id": f"tool-{index:04d}",
                            "type": "toolCall",
                            "toolName": "bash",
                            "path": f"src/file-{index % 3}.py",
                            "text": f"message {index}",
                        }
                        for index in range(item_count)
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    items = await handlers.history(HistoryInput(lane="@alpha", view="items", limit=1200), ctx)
    tools = await handlers.history(HistoryInput(lane="@alpha", view="tools"), ctx)
    files = await handlers.history(HistoryInput(lane="@alpha", view="files"), ctx)

    assert len(items.items) == 1200
    assert items.items[0].text == "message 5"
    assert items.items[-1].text == "message 1204"
    assert [tool.tool for tool in tools.tools] == ["bash"]
    assert tools.tools[0].count == item_count
    assert sum(file.count for file in files.files) == item_count


async def test_history_refresh_preserves_live_items_omitted_from_replay(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [
                        {"id": "old", "type": "agentMessage", "text": "old"},
                        {"id": "keep", "type": "agentMessage", "text": "keep"},
                    ],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)
    await handlers.history(HistoryInput(lane="@alpha", view="items"), ctx)

    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "items": [{"id": "keep", "type": "agentMessage", "text": "keep"}],
                }
            ],
        }
    }

    normal = await handlers.history(HistoryInput(lane="@alpha", view="items"), ctx)
    raw = await handlers.history(HistoryInput(lane="@alpha", view="items", raw=True), ctx)

    assert [item.item_id for item in normal.items] == ["keep", "old"]
    assert [item.item_id for item in raw.items] == ["keep"]
    indexed_items = await store.list_thread_items(lane="lane-1", limit=None)
    assert [item.item_id for item in indexed_items] == ["old", "keep"]


async def test_history_summary_and_items_share_additive_canonical_index(store: Registry) -> None:
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "lane-1",
            "turns": [
                {
                    "id": "t1",
                    "status": "completed",
                    "items": [{"id": "msg-1", "type": "agentMessage", "text": "done"}],
                }
            ],
        }
    }
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)
    await store.upsert_thread_item(
        thread_item(
            lane="lane-1",
            provider_thread_id="lane-1",
            turn_id="t1",
            item_id="cmd-live",
        ).model_copy(
            update={
                "item_type": "commandExecution",
                "tool": "shell",
                "command": "pwd",
                "status": "completed",
                "success": True,
            }
        )
    )

    summary = await handlers.history(HistoryInput(lane="@alpha"), ctx)
    items = await handlers.history(HistoryInput(lane="@alpha", view="items"), ctx)

    assert summary.thread is not None
    assert (summary.thread.items, summary.thread.tool_calls, summary.thread.unique_tools) == (
        2,
        1,
        ["shell"],
    )
    assert {item.item_id for item in items.items} == {"cmd-live", "msg-1"}


async def test_history_filters_and_projects_canonical_metadata(store: Registry) -> None:
    client = FakeLaneClient()
    payload = load_json("app_server", "thread_read", "canonical_items_v0144.json")
    thread = payload["thread"]
    assert isinstance(thread, dict)
    thread["id"] = "lane-1"
    client.read_result = payload
    ctx = make_ctx(store, client)
    await handlers.open_lane(OpenInput(name="alpha"), ctx)

    tools = await handlers.history(
        HistoryInput(
            lane="@alpha",
            view="items",
            tool_server="linear",
            tool_status="completed",
            arg_key="id",
        ),
        ctx,
    )
    children = await handlers.history(
        HistoryInput(
            lane="@alpha",
            view="items",
            mentions_thread="019f0000-0000-7000-9000-000000000099",
        ),
        ctx,
    )
    summary = await handlers.history(HistoryInput(lane="@alpha"), ctx)

    assert [item.item_id for item in tools.items] == ["i-mcp"]
    tool = tools.items[0]
    assert (tool.tool, tool.tool_server, tool.tool_status, tool.arguments) == (
        "save_issue",
        "linear",
        "completed",
        {"id": "DIS-44"},
    )
    assert {item.item_id for item in children.items} == {"i-collab", "i-subagent"}
    assert all(item.child_thread_ids for item in children.items)
    assert summary.thread is not None
    assert summary.thread.subagents_count == 1
    assert summary.thread.subagent_thread_ids == ["019f0000-0000-7000-9000-000000000099"]

    sender = await handlers.history(
        HistoryInput(
            lane="@alpha",
            view="items",
            mentions_thread="019f0000-0000-7000-9000-000000000044",
        ),
        ctx,
    )
    assert [item.item_id for item in sender.items] == ["i-collab"]

    large = load_json("app_server", "thread_read", "canonical_items_v0144.json")
    large_thread = large["thread"]
    assert isinstance(large_thread, dict)
    large_thread["id"] = "lane-1"
    turns = large_thread["turns"]
    assert isinstance(turns, list) and isinstance(turns[0], dict)
    items = turns[0]["items"]
    assert isinstance(items, list)
    mcp = next(item for item in items if isinstance(item, dict) and item.get("id") == "i-mcp")
    assert isinstance(mcp, dict)
    mcp["arguments"] = {"id": "DIS-44", "blob": "x" * 100_000}
    client.read_result = large
    bounded = await handlers.history(HistoryInput(lane="@alpha", view="items", arg_key="id"), ctx)
    assert [item.item_id for item in bounded.items] == ["i-mcp"]
    assert bounded.items[0].argument_keys == ["blob", "id"]


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
            last_turn_id="turn-7",
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
        and kw["last_turn_id"] == "turn-7"
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


async def test_send_to_unmanaged_thread_registers_and_indexes_before_policy_gate(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.threads["raw-thread"] = ThreadInfo(
        id="raw-thread",
        name="@desktop",
        cwd="/workspace",
        preview="existing thread",
    )
    ctx = make_ctx(store, client)

    with pytest.raises(AuthorityError) as exc:
        await handlers.send(LaneTextInput(lane="raw-thread", text="hello"), ctx)

    assert "source=attached" in str(exc.value)
    lane = await store.find_lane("raw-thread")
    assert lane is not None
    assert lane.source == "attached"
    assert lane.handle == "@desktop"
    sync = await store.get_lane_sync("raw-thread")
    assert sync is not None
    assert sync.state == "metadata"
    assert sync.preview == "existing thread"
    log = await store.recent_actions(limit=5)
    assert any(action.op == "send-manage" and action.lane == "raw-thread" for action in log)
    assert any(
        name == "thread_read" and kw["thread_id"] == "raw-thread" and kw["include_turns"] is False
        for name, kw in client.calls
    )
    assert not any(name == "turn_start" for name, _ in client.calls)


async def test_send_to_unmanaged_thread_registers_then_sends_when_policy_allows(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.threads["raw-thread"] = ThreadInfo(id="raw-thread", name="@desktop", cwd="/workspace")
    ctx = make_ctx(store, client, policy=RuntimePolicy(allow_attached_writes=True))

    sent = await handlers.send(LaneTextInput(lane="raw-thread", text="hello"), ctx)

    assert sent.accepted is True
    assert sent.id == "raw-thread"
    assert sent.source == "attached"
    assert sent.writable is True
    assert sent.capabilities.send is True
    assert [name for name, _ in client.calls][:4] == [
        "thread_read",
        "thread_resume",
        "thread_resume",
        "turn_start",
    ]
    assert any(
        name == "turn_start"
        and kw["thread_id"] == "raw-thread"
        and kw["text"] == "hello"
        and kw["cwd"] == "/workspace"
        for name, kw in client.calls
    )
    receipts = await store.list_message_receipts(lane="raw-thread")
    assert len(receipts) == 1
    assert receipts[0].status == "sent"


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
    assert status.active == 1
    assert status.waiting_approval == 0
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


def _thread_history(
    *, thread_id: str = "thread", tool: str, path: str = "src/app.py"
) -> dict[str, object]:
    return {
        "thread": {
            "id": thread_id,
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
            dirty.id: _thread_history(thread_id=dirty.id, tool="bash", path="README.md"),
            clean.id: _thread_history(thread_id=clean.id, tool="python", path="src/app.py"),
        }
    )
    ctx = make_ctx(store, client)
    await handlers.history(HistoryInput(lane="@dirty"), ctx)
    await handlers.history(HistoryInput(lane="@clean"), ctx)
    client.calls.clear()

    out = await handlers.history(HistoryInput(has_tool="bash", changed=True), ctx)

    assert not any(name == "thread_read" for name, _ in client.calls)
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
    assert any(
        name == "thread_resume"
        and kw["exclude_turns"] is True
        and kw["initial_turns_page"] is not None
        for name, kw in client.calls
    )


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


async def test_sync_persists_bounded_history_continuation_across_calls(
    store: Registry,
) -> None:
    class PagedClient(FakeLaneClient):
        async def thread_resume_full(self, thread_id: str, **kwargs: object) -> ThreadResumeResult:
            self._record("thread_resume_full", thread_id=thread_id, **kwargs)
            initial = kwargs.get("initial_turns_page")
            return ThreadResumeResult(
                thread=ThreadInfo(id=thread_id),
                initial_turns_page=(
                    ThreadTurnsPage(
                        data=[ThreadTurn(id="turn-new", status="completed")],
                        next_cursor="older",
                        backwards_cursor="newer",
                    )
                    if initial is not None
                    else None
                ),
            )

        async def thread_items_list(self, thread_id: str, **kwargs: object) -> ThreadItemsPage:
            self._record("thread_items_list", thread_id=thread_id, **kwargs)
            return ThreadItemsPage()

        async def thread_turns_list(self, thread_id: str, **kwargs: object) -> ThreadTurnsPage:
            self._record("thread_turns_list", thread_id=thread_id, **kwargs)
            assert kwargs["cursor"] == "older"
            return ThreadTurnsPage()

    client = PagedClient()
    client.read_result = {"thread": {"id": "T9"}}
    ctx = make_ctx(store, client)
    await store.add_lane(id="T9", handle="@desktop", source="attached")

    first = await handlers.sync_lane(LaneSyncInput(lane="T9", max_turns=1, max_items=10), ctx)
    persisted = await store.get_lane_sync("T9")
    assert first.sync.history_complete is False
    assert first.sync.truncated is True
    assert persisted is not None
    assert persisted.history_cursor == "older"
    assert persisted.history_item_turn_id == "turn-new"

    second = await handlers.sync_lane(LaneSyncInput(lane="T9", max_turns=1, max_items=10), ctx)
    persisted = await store.get_lane_sync("T9")
    assert second.sync.history_complete is True
    assert second.sync.truncated is False
    assert persisted is not None
    assert persisted.history_cursor is None
    assert persisted.history_item_turn_id is None
    assert [turn.turn_id for turn in await store.list_thread_turns(lane="T9")] == ["turn-new"]


async def test_sync_prioritizes_recent_provider_history_over_large_local_source(
    store: Registry, tmp_path: Path
) -> None:
    path = tmp_path / "large-rollout.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "T9", "base_instructions": "x" * 2_000},
            }
        )
        + "\n"
    )

    class RecentClient(FakeLaneClient):
        async def thread_resume_full(self, thread_id: str, **kwargs: object) -> ThreadResumeResult:
            self._record("thread_resume_full", thread_id=thread_id, **kwargs)
            return ThreadResumeResult(
                thread=ThreadInfo(id=thread_id),
                initial_turns_page=ThreadTurnsPage(
                    data=[ThreadTurn(id="turn-recent", status="completed")]
                ),
            )

        async def thread_items_list(self, thread_id: str, **kwargs: object) -> ThreadItemsPage:
            self._record("thread_items_list", thread_id=thread_id, **kwargs)
            return ThreadItemsPage()

    client = RecentClient()
    client.read_result = {"thread": {"id": "T9", "path": str(path)}}
    ctx = make_ctx(store, client)
    await store.add_lane(id="T9", handle="@desktop", source="attached")

    out = await handlers.sync_lane(
        LaneSyncInput(lane="T9", max_bytes=256, max_turns=10, max_items=10), ctx
    )

    assert out.sync.history_complete is True
    assert [turn.turn_id for turn in await store.list_thread_turns(lane="T9")] == ["turn-recent"]
    assert out.sync.scanned_bytes <= 256
    assert out.sync.state == "partial"


async def test_sync_max_seconds_bounds_metadata_read(store: Registry) -> None:
    class SlowReadClient(FakeLaneClient):
        async def thread_read(
            self, thread_id: str, include_turns: bool = False
        ) -> dict[str, object]:
            await asyncio.sleep(0.05)
            return await super().thread_read(thread_id, include_turns)

    client = SlowReadClient()
    ctx = make_ctx(store, client)
    await store.add_lane(id="T9", handle="@desktop", source="attached")

    with pytest.raises(AppServerError, match=r"sync timed out after 0\.001s"):
        await handlers.sync_lane(
            LaneSyncInput(lane="T9", max_seconds=0.001),
            ctx,
        )


async def test_sync_raw_unmanaged_thread_registers_attached_lane(
    store: Registry, tmp_path: Path
) -> None:
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        '{"type":"session_meta","timestamp":"2026-06-05T10:00:00.000Z",'
        '"payload":{"id":"T9","cwd":"/work"}}\n'
    )
    client = FakeLaneClient()
    client.read_result = {
        "thread": {
            "id": "T9",
            "name": "Desktop",
            "cwd": "/work",
            "path": str(path),
        }
    }
    ctx = make_ctx(store, client)

    out = await handlers.sync_lane(LaneSyncInput(lane="T9", full=True), ctx)

    assert out.lane == "T9"
    assert out.source == "attached"
    assert out.handle == "Desktop"
    assert out.writable is False
    assert out.sync.state == "complete"
    lane = await store.get_lane("T9")
    assert lane.source == "attached"
    assert lane.status == "idle"
    assert [name for name, _ in client.calls].count("thread_read") == 2
    assert any(
        name == "thread_resume"
        and kw["exclude_turns"] is True
        and kw["initial_turns_page"] is not None
        for name, kw in client.calls
    )


async def test_sync_reconciles_archived_membership(store: Registry) -> None:
    client = FakeLaneClient()
    client.list_results_by_archived[True] = [ThreadInfo(id="T9")]
    client.list_results_by_archived[False] = []
    ctx = make_ctx(store, client)
    await store.add_lane(id="T9", handle="@desktop", source="attached", status="idle")

    out = await handlers.sync_lane(LaneSyncInput(lane="T9"), ctx)

    assert out.status == "archived"
    assert (await store.get_lane("T9")).status == "archived"
    provider_thread = await store.get_provider_thread("codex", "T9")
    assert provider_thread is not None
    assert provider_thread.lifecycle_state == "archived"
    assert any(
        name == "thread_list"
        and kw["archived"] is True
        and kw["search_term"] == "T9"
        and kw["use_state_db_only"] is True
        for name, kw in client.calls
    )


async def test_sync_reconciles_restored_membership(store: Registry) -> None:
    client = FakeLaneClient()
    client.list_results_by_archived[True] = []
    client.list_results_by_archived[False] = [ThreadInfo(id="T9")]
    ctx = make_ctx(store, client)
    await store.add_lane(id="T9", handle="@desktop", source="attached", status="archived")

    out = await handlers.sync_lane(LaneSyncInput(lane="T9"), ctx)

    assert out.status == "idle"
    assert (await store.get_lane("T9")).status == "idle"
    provider_thread = await store.get_provider_thread("codex", "T9")
    assert provider_thread is not None
    assert provider_thread.lifecycle_state == "active"


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


async def test_discover_excludes_managed_threads_without_changing_authority(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    client.list_result = [ThreadInfo(id="managed"), ThreadInfo(id="unmanaged")]
    ctx = make_ctx(store, client)
    await store.add_lane(id="managed", handle="@managed", source="attached", status="idle")

    out = await handlers.discover(DiscoverInput(), ctx)

    assert [session.id for session in out.sessions] == ["unmanaged"]
    assert await store.find_lane("unmanaged") is None
    assert await store.get_provider_thread("codex", "unmanaged") is not None


async def test_show_refreshes_bounded_descendant_topology(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    root = await handlers.open_lane(OpenInput(name="root"), ctx)
    client.list_result = [
        ThreadInfo(
            id="child",
            parent_thread_id=root.id,
            source={
                "subAgent": {
                    "thread_spawn": {
                        "parent_thread_id": root.id,
                        "depth": 1,
                        "agent_nickname": "Scout",
                    }
                }
            },
        ),
        ThreadInfo(id="grandchild", parent_thread_id="child"),
        ThreadInfo(id="fork", forked_from_id=root.id),
    ]

    out = await handlers.show(ShowInput(lane=root.ref, topology=True, topology_limit=10), ctx)

    assert [node.id for node in out.topology.children] == ["child"]
    assert [node.id for node in out.topology.descendants] == ["child", "grandchild"]
    assert out.topology.children[0].agent_nickname == "Scout"
    assert all(node.id != "fork" for node in out.topology.descendants)
    assert any(
        name == "thread_list" and kwargs["ancestor_thread_id"] == root.id and kwargs["limit"] == 10
        for name, kwargs in client.calls
    )


async def test_roster_filters_managed_descendants_and_includes_root(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    root = await handlers.open_lane(OpenInput(name="root"), ctx)
    await store.add_lane(id="child", handle="@child", source="attached", status="idle")
    await store.add_lane(id="other", handle="@other", source="attached", status="idle")
    client.list_result = [ThreadInfo(id="child", parent_thread_id=root.id)]

    out = await handlers.roster(RosterInput(root=root.ref), ctx)

    assert {lane.id for lane in out.lanes} == {root.id, "child"}
    assert any(
        name == "thread_list" and kwargs["ancestor_thread_id"] == root.id
        for name, kwargs in client.calls
    )


async def test_roster_topology_refresh_reconciles_lifecycle_both_directions(
    store: Registry,
) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    root = await handlers.open_lane(OpenInput(name="root"), ctx)
    await store.add_lane(id="child", handle="@child", source="attached", status="idle")
    child = ThreadInfo(id="child", parent_thread_id=root.id)
    client.list_results_by_archived[False] = [child]
    client.list_results_by_archived[True] = []

    await handlers.roster(RosterInput(root=root.ref, include_archived=True), ctx)

    observed = await store.get_provider_thread("codex", "child")
    assert observed is not None
    assert observed.lifecycle_state == "active"

    client.list_results_by_archived[False] = []
    client.list_results_by_archived[True] = [child]
    await handlers.roster(RosterInput(root=root.ref, include_archived=True), ctx)

    observed = await store.get_provider_thread("codex", "child")
    assert observed is not None
    assert observed.lifecycle_state == "archived"

    client.list_results_by_archived[False] = [child]
    client.list_results_by_archived[True] = []
    await handlers.roster(RosterInput(root=root.ref, include_archived=True), ctx)

    observed = await store.get_provider_thread("codex", "child")
    assert observed is not None
    assert observed.lifecycle_state == "active"


async def test_discover_uses_native_parent_filter(store: Registry) -> None:
    client = FakeLaneClient()
    client.list_result = [
        ThreadInfo(id="child", parent_thread_id="root"),
        ThreadInfo(id="other", parent_thread_id="elsewhere"),
    ]
    ctx = make_ctx(store, client)

    out = await handlers.discover(DiscoverInput(parent="root"), ctx)

    assert [session.id for session in out.sessions] == ["child"]
    assert out.sessions[0].topology.parent is None
    assert any(
        name == "thread_list" and kwargs["parent_thread_id"] == "root"
        for name, kwargs in client.calls
    )


async def test_discover_can_list_archived_unmanaged_sessions(store: Registry) -> None:
    client = FakeLaneClient()
    client.list_results_by_archived[True] = [
        ThreadInfo(id="archived-thread", name="Old work", status=ThreadStatus(type="idle"))
    ]
    ctx = make_ctx(store, client)

    out = await handlers.discover(DiscoverInput(limit=5, archived=True), ctx)

    assert len(out.sessions) == 1
    assert out.sessions[0].id == "archived-thread"
    assert out.sessions[0].archived is True
    assert any(
        name == "thread_list"
        and kw["limit"] == 5
        and kw["archived"] is True
        and kw["use_state_db_only"] is True
        for name, kw in client.calls
    )


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


async def test_query_reads_indexed_managed_history_without_app_server_search(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    lane = await store.add_lane(id="L1", handle="@local", source="own", cwd=str(repo))
    await store.upsert_thread_turn(
        thread_turn(lane=lane.id, provider_thread_id=lane.id, turn_id="turn-1")
    )
    await store.upsert_thread_item(
        thread_item(
            lane=lane.id,
            provider_thread_id=lane.id,
            turn_id="turn-1",
            item_id="item-1",
        ).model_copy(update={"text": "local needle lives in normalized history"}),
        refs=[
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="item-1",
                ref_type="file",
                ref_value="src/local.py",
            )
        ],
    )

    out = await handlers.query(QueryInput(query="needle", repo=str(repo)), ctx)

    assert out.experimental is False
    assert out.scanned == 1
    assert [match.ref for match in out.matches] == [lane.ref]
    assert out.matches[0].handle == "@local"
    assert "local needle lives" in out.matches[0].snippet
    assert out.matches[0].files == ["src/local.py"]
    assert not any(name == "thread_search" for name, _ in client.calls)
    assert not any(name == "thread_read" for name, _ in client.calls)


async def test_query_requires_text_or_structural_filter(store: Registry) -> None:
    ctx = make_ctx(store)

    with pytest.raises(ValidationError, match="requires text or at least one structural filter"):
        await handlers.query(QueryInput(), ctx)


async def test_query_archived_filter_matches_app_server_semantics(
    store: Registry,
) -> None:
    ctx = make_ctx(store)
    active = await store.add_lane(id="active", handle="@active", source="own")
    archived = await store.add_lane(
        id="archived", handle="@archived", source="own", status="archived"
    )
    for lane in (active, archived):
        await store.upsert_thread_turn(
            thread_turn(lane=lane.id, provider_thread_id=lane.id, turn_id=f"{lane.id}-turn")
        )
        await store.upsert_thread_item(
            thread_item(
                lane=lane.id,
                provider_thread_id=lane.id,
                turn_id=f"{lane.id}-turn",
                item_id=f"{lane.id}-item",
            ).model_copy(update={"text": "needle in local history"})
        )

    active_out = await handlers.query(QueryInput(query="needle"), ctx)
    archived_out = await handlers.query(QueryInput(query="needle", archived=True), ctx)

    assert [match.ref for match in active_out.matches] == [active.ref]
    assert [match.ref for match in archived_out.matches] == [archived.ref]


async def test_query_filters_structural_refs_and_concrete_tool_metadata(
    store: Registry, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    lane = await store.add_lane(id="L1", handle="@local", source="own", cwd=str(repo))
    await store.upsert_thread_turn(
        thread_turn(lane=lane.id, provider_thread_id=lane.id, turn_id="turn-1")
    )
    payload = {
        "type": "mcpToolCall",
        "id": "tool-1",
        "server": "codex_apps",
        "tool": "linear.save_issue",
        "status": "completed",
        "arguments": {"id": "DIS-31"},
        "durationMs": 123,
    }
    await store.upsert_thread_item(
        thread_item(
            lane=lane.id,
            provider_thread_id=lane.id,
            turn_id="turn-1",
            item_id="tool-1",
        ).model_copy(
            update={
                "item_type": "mcpToolCall",
                "tool": "linear.save_issue",
                "server": "codex_apps",
                "status": "completed",
                "arguments": {"id": "DIS-31"},
                "duration_ms": 123,
                "success": True,
                "payload": payload,
                "raw_retained": True,
            }
        ),
        refs=[
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="tool-1",
                ref_type="tool",
                ref_value="linear.save_issue",
            ),
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="tool-1",
                ref_type="tool_server",
                ref_value="codex_apps",
            ),
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="tool-1",
                ref_type="tool_status",
                ref_value="completed",
            ),
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="tool-1",
                ref_type="tool_arg_key",
                ref_value="id",
            ),
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="tool-1",
                ref_type="file",
                ref_value="src/app.py",
            ),
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="tool-1",
                ref_type="thread",
                ref_value="019f0000-0000-7000-9000-000000000099",
            ),
        ],
    )

    out = await handlers.query(
        QueryInput(
            tool="linear.save_issue",
            tool_server="codex_apps",
            tool_status="completed",
            arg_key="id",
            file="src/app.py",
            raw_retained=True,
            repo=str(repo),
        ),
        ctx,
    )

    assert [match.item_id for match in out.matches] == ["tool-1"]
    match = out.matches[0]
    assert match.tool == "linear.save_issue"
    assert match.tool_server == "codex_apps"
    assert match.tool_status == "completed"
    assert match.duration_ms == 123
    assert match.arguments == {"id": "DIS-31"}
    assert match.success is True
    assert match.files == ["src/app.py"]
    assert {ref.type for ref in match.refs} >= {"tool", "tool_server", "tool_status", "file"}

    child_out = await handlers.query(
        QueryInput(mentions_thread="019f0000-0000-7000-9000-000000000099"), ctx
    )
    assert [match.item_id for match in child_out.matches] == ["tool-1"]

    await store.upsert_thread_item(
        thread_item(
            lane=lane.id,
            provider_thread_id=lane.id,
            turn_id="turn-1",
            item_id="tool-2",
        ).model_copy(
            update={
                "item_type": "mcpToolCall",
                "tool": "linear.save_issue",
                "server": None,
                "status": None,
            }
        ),
        refs=[
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="tool-2",
                ref_type="tool_server",
                ref_value="codex_apps",
            ),
            thread_item_ref(
                provider_thread_id=lane.id,
                item_id="tool-2",
                ref_type="tool_status",
                ref_value="completed",
            ),
        ],
    )

    ref_only_out = await handlers.query(QueryInput(item_id="tool-2"), ctx)

    assert [match.item_id for match in ref_only_out.matches] == ["tool-2"]
    assert ref_only_out.matches[0].tool_server == "codex_apps"
    assert ref_only_out.matches[0].tool_status == "completed"


async def test_query_reports_unsuccessful_command_as_errored(store: Registry) -> None:
    client = FakeLaneClient()
    ctx = make_ctx(store, client)
    lane = await store.add_lane(id="L1", handle="@local", source="own")
    await store.upsert_thread_item(
        thread_item(lane=lane.id, provider_thread_id=lane.id, item_id="failed-command").model_copy(
            update={
                "item_type": "commandExecution",
                "tool": "shell",
                "status": "completed",
                "success": False,
                "error": None,
            }
        )
    )

    out = await handlers.query(QueryInput(errored=True), ctx)

    assert [match.item_id for match in out.matches] == ["failed-command"]
    assert out.matches[0].errored is True


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


async def test_plan_new_lane_rejects_missing_image_before_workspace_mutation(
    store: Registry, tmp_path: Path
) -> None:
    ctx = make_ctx(store, FakeLaneClient())

    with pytest.raises(ValidationError, match="local image not found"):
        await handlers.plan_new_lane(
            NewInput(
                name="preview",
                cwd=str(tmp_path),
                content=[LocalImageContent(path="missing.png")],
            ),
            ctx,
        )

    assert not (tmp_path / ".dispatch").exists()


async def test_plan_new_lane_reports_valid_image_count(store: Registry, tmp_path: Path) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")
    ctx = make_ctx(store, FakeLaneClient())

    plan = await handlers.plan_new_lane(
        NewInput(
            name="preview",
            cwd=str(tmp_path),
            content=[LocalImageContent(path=str(image))],
        ),
        ctx,
    )

    assert plan.would_send is True
    assert plan.image_count == 1
    assert [(item.kind, item.ref) for item in plan.images] == [("local", str(image))]
    assert await store.list_model_catalog() == []


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
