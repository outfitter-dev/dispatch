"""Inbox/subscription delivery helpers.

Subscriptions turn lane events into durable inbox rows, then optionally bridge
those rows into the existing queued-turn path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from outfitter.dispatch.client.events import (
    ApprovalRequested,
    LaneEvent,
    LaneIdle,
    TurnCompleted,
    TurnFailed,
)
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.registry.models import Lane, Subscription

from . import queue
from .message_attribution import codex_thread_link, render_dispatch_message

SubscriptionEvent = Literal["completed", "failed", "idle", "approval", "activity"]


@dataclass(frozen=True)
class SubscriptionEventInfo:
    name: SubscriptionEvent
    turn_id: str | None = None
    request_id: int | None = None
    detail: str | None = None


async def process_event_subscriptions(ctx: Ctx, lane: Lane, event: LaneEvent) -> int:
    info = _event_info(event)
    if info is None:
        return 0
    matched = 0
    for subscription in await ctx.registry.list_subscriptions(target_lane=lane.id, state="active"):
        if not _matches(subscription, info.name):
            continue
        matched += 1
        try:
            await _deliver_subscription(ctx, lane, subscription, info)
        except Exception:
            ctx.log.exception(
                "subscription.delivery_failed",
                subscription=subscription.id,
                lane=lane.id,
                event_name=info.name,
            )
    return matched


def _event_info(event: LaneEvent) -> SubscriptionEventInfo | None:
    if isinstance(event, TurnCompleted):
        return SubscriptionEventInfo("completed", turn_id=event.turn_id)
    if isinstance(event, TurnFailed):
        return SubscriptionEventInfo("failed", turn_id=event.turn_id, detail=event.message)
    if isinstance(event, ApprovalRequested):
        return SubscriptionEventInfo(
            "approval", turn_id=event.turn_id, request_id=event.request_id, detail=event.kind
        )
    if isinstance(event, LaneIdle):
        return SubscriptionEventInfo("idle")
    return SubscriptionEventInfo("activity")


def _matches(subscription: Subscription, event_name: SubscriptionEvent) -> bool:
    match subscription.when:
        case "done":
            return event_name in {"completed", "failed"}
        case "completed":
            return event_name == "completed"
        case "failed":
            return event_name == "failed"
        case "idle":
            return event_name == "idle"
        case "approval" | "needs-attention":
            return event_name == "approval"
        case "activity":
            return True


async def _deliver_subscription(
    ctx: Ctx, target: Lane, subscription: Subscription, event: SubscriptionEventInfo
) -> None:
    subscriber = await ctx.registry.get_lane(subscription.subscriber_lane)
    tail = await _tail_text(ctx, target, subscription.tail)
    subject = f"{target.handle} {event.name}"
    body = _message_body(target, subscription, event, tail)
    message = await ctx.registry.add_inbox_message(
        recipient_lane=subscriber.id,
        source_lane=target.id,
        subscription_id=subscription.id,
        kind="subscription_update",
        subject=subject,
        body=body,
        payload={
            "target_lane": target.id,
            "target_ref": target.ref,
            "subscriber_lane": subscriber.id,
            "subscriber_ref": subscriber.ref,
            "when": subscription.when,
            "event": event.name,
            "turn_id": event.turn_id,
            "request_id": event.request_id,
        },
        delivery=subscription.delivery,
    )
    if subscription.delivery == "turn":
        queued = await ctx.registry.enqueue_message(lane=subscriber.id, text=body)
        await ctx.registry.mark_inbox_delivered(message.id, queued_message_id=queued.id, ack=False)
        if subscription.deliver == "now" or subscriber.status == "idle":
            await queue.drain_next_queued_message(ctx, subscriber.id)
    await ctx.registry.mark_subscription_matched(subscription.id, inbox_message_id=message.id)
    await ctx.registry.log_action(
        "subscribe",
        lane=target.id,
        detail=f"{subscription.when}->{subscriber.ref}:{subscription.delivery}",
    )


async def _tail_text(ctx: Ctx, lane: Lane, tail: int) -> str | None:
    if tail <= 0:
        return None
    try:
        result = await ctx.client.thread_read(lane.id, include_turns=True)
    except Exception as exc:
        ctx.log.warning("subscription.tail_read_failed", lane=lane.id, error=str(exc))
        return None
    texts = _extract_texts(result)
    selected = [text for text in texts if text.strip()][-tail:]
    return "\n\n".join(selected) if selected else None


def _message_body(
    target: Lane, subscription: Subscription, event: SubscriptionEventInfo, tail: str | None
) -> str:
    lines: list[str] = []
    if event.turn_id:
        lines.append(f"Turn: {event.turn_id}")
    if event.detail:
        lines.append(f"Detail: {event.detail}")
    if tail:
        lines.extend(["", "Latest message:", tail])
    body = "\n".join(lines)
    if not subscription.attribution:
        plain = [
            f"[dispatch] Subscription update for {target.handle} ({target.ref})",
            f"Event: {event.name}",
            f"When: {subscription.when}",
        ]
        if body:
            plain.extend(["", body])
        return "\n".join(plain)
    return render_dispatch_message(
        body=body,
        kind="sub",
        source=codex_thread_link(target.handle, target.id),
        ref=target.ref,
        details=(event.name, subscription.when),
    )


def _extract_texts(value: object) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "text" and isinstance(item, str):
                texts.append(item)
            else:
                texts.extend(_extract_texts(item))
    elif isinstance(value, list):
        for item in value:
            texts.extend(_extract_texts(item))
    return texts
