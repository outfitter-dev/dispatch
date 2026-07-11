"""Turn-driving helpers for integration tests (dogfooding the typed client)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.events import ApprovalRequested
from outfitter.dispatch.client.models import Decision, SandboxPolicy, UserInput


def _delta_text(message: dict[str, object]) -> str:
    if message.get("method") != "item/agentMessage/delta":
        return ""
    params = message.get("params")
    if not isinstance(params, dict):
        return ""
    return str(params.get("delta", params.get("text", "")))


def _completed_agent_text(message: dict[str, object]) -> str | None:
    """The authoritative agent text from an ``item/completed`` agentMessage item."""
    if message.get("method") != "item/completed":
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    item = params.get("item")
    if isinstance(item, dict) and item.get("type") == "agentMessage":
        text = item.get("text")
        return text if isinstance(text, str) else None
    return None


async def run_turn(
    client: AppServerClient,
    lane: str,
    text: str,
    cwd: str,
    *,
    input_items: list[UserInput] | None = None,
    timeout: float = 150,
) -> str:
    """Start a low-effort read-only turn; return the agent text once
    ``turn/completed`` arrives (authoritative item text, else accumulated deltas)."""
    raw = client.raw_events(lane)  # eager subscription before the turn starts
    await client.turn_start(lane, text, cwd, input_items=input_items, effort="low")
    return await collect_turn(raw, timeout=timeout)


async def collect_turn(raw: AsyncIterator[dict[str, object]], *, timeout: float = 150) -> str:
    """Collect authoritative assistant text from an already-subscribed turn stream."""
    deltas = ""
    final: str | None = None
    async with asyncio.timeout(timeout):
        async for message in raw:
            deltas += _delta_text(message)
            final = _completed_agent_text(message) or final
            if message.get("method") == "turn/completed":
                break
    return final if final is not None else deltas


async def run_turn_autoapprove(
    client: AppServerClient,
    lane: str,
    text: str,
    cwd: str,
    *,
    decision: Decision = "accept",
    timeout: float = 200,
) -> str:
    """Run a workspace-write turn, auto-answering every approval request with
    ``decision``; return agent text once the turn completes."""
    raw = client.raw_events(lane)
    events = client.events(lane)
    await client.turn_start(
        lane,
        text,
        cwd,
        approval_policy="untrusted",
        sandbox_policy=SandboxPolicy(type="workspaceWrite"),
        effort="low",
    )

    async def _approver() -> None:
        async for event in events:
            if isinstance(event, ApprovalRequested):
                await client.respond_approval(event.request_id, decision)

    approver = asyncio.create_task(_approver())
    deltas = ""
    final: str | None = None
    try:
        async with asyncio.timeout(timeout):
            async for message in raw:
                deltas += _delta_text(message)
                final = _completed_agent_text(message) or final
                if message.get("method") == "turn/completed":
                    break
    finally:
        approver.cancel()
    return final if final is not None else deltas
