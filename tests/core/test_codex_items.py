"""Canonical Codex item normalization and ingestion convergence."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from outfitter.dispatch.client.events import ItemCompleted, project_notification
from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.core.codex_items import CODEX_ITEM_TYPES, normalize_codex_item
from outfitter.dispatch.core.event_index import index_codex_lane_event
from outfitter.dispatch.core.history_index import index_codex_thread_read
from outfitter.dispatch.registry.store import Registry
from tests.fixtures import load_json

THREAD_ID = "019f0000-0000-7000-9000-000000000044"


def _items() -> list[dict[str, object]]:
    payload = load_json("app_server", "thread_read", "canonical_items_v0144.json")
    thread = cast(dict[str, object], payload["thread"])
    turn = cast(list[dict[str, object]], thread["turns"])[0]
    return cast(list[dict[str, object]], turn["items"])


def test_fixture_has_explicit_disposition_for_every_v0144_item_type() -> None:
    assert {str(item["type"]) for item in _items()} == CODEX_ITEM_TYPES


def test_canonical_normalization_extracts_provider_neutral_fields_and_refs() -> None:
    by_type = {str(raw["type"]): raw for raw in _items()}
    policy = CapturePolicy(mode="debug")

    command, command_refs = normalize_codex_item(
        by_type["commandExecution"],
        provider_thread_id=THREAD_ID,
        lane=THREAD_ID,
        turn_id="turn-canonical",
        inserted_at="2026-07-10T00:00:00Z",
        position=5,
        capture=policy,
    )
    assert (command.tool, command.command, command.cwd, command.status) == (
        "shell",
        "uv run pytest",
        "/fixture/dispatch",
        "completed",
    )
    assert command.success is True
    assert command.duration_ms == 120
    assert ("cwd", "/fixture/dispatch") in {(ref.ref_type, ref.ref_value) for ref in command_refs}

    mcp, mcp_refs = normalize_codex_item(
        by_type["mcpToolCall"],
        provider_thread_id=THREAD_ID,
        lane=THREAD_ID,
        turn_id="turn-canonical",
        inserted_at="2026-07-10T00:00:00Z",
        position=7,
        capture=policy,
    )
    assert (mcp.tool, mcp.server, mcp.arguments) == (
        "save_issue",
        "linear",
        {"id": "DIS-44"},
    )
    assert {("tool_server", "linear"), ("tool_arg_key", "id")} <= {
        (ref.ref_type, ref.ref_value) for ref in mcp_refs
    }

    collab, collab_refs = normalize_codex_item(
        by_type["collabAgentToolCall"],
        provider_thread_id=THREAD_ID,
        lane=THREAD_ID,
        turn_id="turn-canonical",
        inserted_at="2026-07-10T00:00:00Z",
        position=9,
        capture=policy,
    )
    assert collab.tool == "spawnAgent"
    assert ("child_thread", "019f0000-0000-7000-9000-000000000099") in {
        (ref.ref_type, ref.ref_value) for ref in collab_refs
    }


def test_unknown_future_item_remains_visible_without_raw_capture() -> None:
    item, refs = normalize_codex_item(
        {"id": "future-1", "type": "futureItem", "text": "still visible"},
        provider_thread_id=THREAD_ID,
        lane=THREAD_ID,
        turn_id="turn-1",
        inserted_at="2026-07-10T00:00:00Z",
        position=0,
        capture=CapturePolicy(mode="standard"),
    )
    assert (item.item_type, item.text, item.raw_retained, refs) == (
        "futureItem",
        "still visible",
        False,
        [],
    )


def test_canonical_sensitive_fields_are_bounded_and_redacted() -> None:
    item, refs = normalize_codex_item(
        {
            "id": "sensitive-1",
            "type": "mcpToolCall",
            "server": "example",
            "tool": "call",
            "status": "failed",
            "command": "--token supersecret " + "x" * 100,
            "cwd": "/tmp/" + "x" * 100,
            "arguments": {"api_key": "supersecret", "prompt": "password=hunter2"},
            "error": {"message": "authorization: secret-value " + "x" * 100},
        },
        provider_thread_id=THREAD_ID,
        lane=THREAD_ID,
        turn_id="turn-1",
        inserted_at="2026-07-10T00:00:00Z",
        position=0,
        capture=CapturePolicy(max_text_bytes=32, max_payload_bytes=96),
    )
    serialized = str(item.model_dump())
    assert "supersecret" not in serialized
    assert "hunter2" not in serialized
    assert "secret-value" not in serialized
    assert item.command is not None and len(item.command.encode()) <= 32
    assert item.cwd is not None and len(item.cwd.encode()) <= 32
    assert item.error is not None and len(item.error.encode()) <= 32
    assert ("tool_arg_key", "api_key") in {(ref.ref_type, ref.ref_value) for ref in refs}


@pytest.mark.asyncio
async def test_live_item_and_thread_read_converge_idempotently(tmp_path: Path) -> None:
    registry = await Registry.open(tmp_path / "registry.db")
    lane = await registry.add_lane(
        id=THREAD_ID,
        handle="@canonical",
        source="own",
        cwd="/fixture/dispatch",
    )
    payload = load_json("app_server", "thread_read", "canonical_items_v0144.json")
    raw = _items()[7]
    events = project_notification(
        "item/completed",
        {"threadId": THREAD_ID, "turnId": "turn-canonical", "item": raw},
    )
    event = events[0]
    assert isinstance(event, ItemCompleted)
    await index_codex_lane_event(registry, lane, event, CapturePolicy(mode="debug"))
    live = await registry.get_thread_item(
        provider="codex", provider_thread_id=THREAD_ID, item_id="i-mcp"
    )

    await index_codex_thread_read(registry, lane, payload, CapturePolicy(mode="debug"))
    replayed = await registry.get_thread_item(
        provider="codex", provider_thread_id=THREAD_ID, item_id="i-mcp"
    )
    assert replayed.model_copy(update={"position": None}) == live

    await index_codex_thread_read(registry, lane, payload, CapturePolicy(mode="debug"))
    again = await registry.get_thread_item(
        provider="codex", provider_thread_id=THREAD_ID, item_id="i-mcp"
    )
    assert again == replayed

    await index_codex_thread_read(registry, lane, payload, CapturePolicy(mode="standard"))
    lower_retention_replay = await registry.get_thread_item(
        provider="codex", provider_thread_id=THREAD_ID, item_id="i-mcp"
    )
    assert lower_retention_replay.payload == replayed.payload
    assert lower_retention_replay.raw_retained is True

    await index_codex_thread_read(registry, lane, payload, CapturePolicy(mode="minimal"))
    preserved = await registry.get_thread_item(
        provider="codex", provider_thread_id=THREAD_ID, item_id="i-mcp"
    )
    assert preserved == replayed

    started = project_notification(
        "item/started",
        {
            "threadId": THREAD_ID,
            "turnId": "turn-canonical",
            "item": {
                "id": "i-mcp",
                "type": "mcpToolCall",
                "server": "linear",
                "tool": "save_issue",
                "status": "inProgress",
                "arguments": {},
            },
        },
    )[0]
    await index_codex_lane_event(registry, lane, started, CapturePolicy(mode="debug"))
    after_stale_start = await registry.get_thread_item(
        provider="codex", provider_thread_id=THREAD_ID, item_id="i-mcp"
    )
    assert after_stale_start == replayed

    partial_payload: dict[str, object] = {
        "thread": {
            "id": THREAD_ID,
            "turns": [
                {
                    "id": "turn-canonical",
                    "status": "completed",
                    "items": [
                        {
                            "id": "persisted-message-1",
                            "type": "agentMessage",
                            "text": "A partial persisted view",
                        }
                    ],
                }
            ],
        }
    }
    await index_codex_thread_read(registry, lane, partial_payload, CapturePolicy(mode="standard"))
    assert (
        await registry.get_thread_item(
            provider="codex", provider_thread_id=THREAD_ID, item_id="i-mcp"
        )
        == replayed
    )
    await registry.close()
