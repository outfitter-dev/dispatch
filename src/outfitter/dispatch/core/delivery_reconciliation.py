"""Bounded provider evidence checks; absence never authorizes resubmission."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from outfitter.dispatch.client.errors import ClientError
from outfitter.dispatch.client.models import ThreadTurn
from outfitter.dispatch.contracts.context import Ctx
from outfitter.dispatch.contracts.errors import CapabilityUnavailableError
from outfitter.dispatch.registry.models import Lane
from outfitter.dispatch.registry.observations import ProviderCorrelation, ProviderObservation

from .providers import ProviderAction, ProviderRoute, route_lane

MAX_CHECKS = 3
MAX_PAGES = 4
MAX_HISTORY_BYTES = 1_000_000
MAX_READINESS_CHECKS = 3


def _evidence_route(ctx: Ctx, lane: Lane | None, *, native_queue: bool) -> ProviderRoute | None:
    if lane is None:
        return None
    try:
        return route_lane(
            ctx,
            lane,
            ProviderAction.QUEUE_EVIDENCE if native_queue else ProviderAction.SYNC,
        )
    except CapabilityUnavailableError:
        return None


async def reconcile_pending(ctx: Ctx) -> None:
    """Check independent threads concurrently; each receipt has a finite budget."""
    unresolved = await ctx.registry.list_unresolved_deliveries()
    waiting = await ctx.registry.list_waiting_deliveries()
    lanes = {receipt.lane for receipt in unresolved + waiting}

    async def process_lane(lane_id: str) -> None:
        from .delivery import submit_reserved
        from .queue import drain_next_queued_message

        for receipt in unresolved:
            if receipt.lane == lane_id:
                await reconcile_receipt(receipt.id, ctx)
        lane = await ctx.registry.find_lane(lane_id)
        if lane is None or lane.status == "archived":
            return
        for receipt in waiting:
            if receipt.lane == lane_id and receipt.transport == "native_queue":
                await submit_reserved(receipt.id, ctx)
        if lane.status != "idle":
            return
        direct = next(
            (
                r
                for r in waiting
                if r.lane == lane_id and r.queue_id is None and r.transport == "turn"
            ),
            None,
        )
        if direct is not None and await submit_reserved(direct.id, ctx):
            return
        await drain_next_queued_message(ctx, lane_id)

    results = await asyncio.gather(*(process_lane(lane) for lane in lanes), return_exceptions=True)
    for lane, result in zip(lanes, results, strict=True):
        if isinstance(result, Exception):
            ctx.log.warning("delivery.lane_check_failed", lane=lane, error=str(result))


async def run_reconciliation(ctx: Ctx) -> None:
    """Periodic local work ends its provider reads after each receipt's budget."""
    while not ctx.abort.is_set():
        try:
            await reconcile_pending(ctx)
        except Exception:
            ctx.log.exception("delivery.reconciliation_failed")
        await asyncio.sleep(2)


async def reconcile_accepted_after_reconnect(ctx: Ctx) -> None:
    """ACKs survive reconnect even when their completion/idle notifications did not."""
    receipts = await ctx.registry.list_accepted_unfinished_deliveries()
    results = await asyncio.gather(
        *(reconcile_receipt(receipt.id, ctx, automatic=False) for receipt in receipts),
        return_exceptions=True,
    )
    for receipt, result in zip(receipts, results, strict=True):
        if isinstance(result, Exception):
            ctx.log.warning(
                "delivery.restore_check_failed", delivery_id=receipt.id, error=str(result)
            )


async def reconcile_receipt(delivery_id: str, ctx: Ctx, *, automatic: bool = True) -> None:
    receipt = await ctx.registry.get_delivery(delivery_id)
    lane = await ctx.registry.find_lane(receipt.lane)
    route = _evidence_route(ctx, lane, native_queue=receipt.transport == "native_queue")
    if route is None:
        return
    if receipt.status == "completed" and receipt.execution_status == "completed":
        if (
            not automatic
            and receipt.transport == "turn"
            and await _refresh_idle_readiness(receipt.lane, ctx)
        ):
            from .queue import drain_next_queued_message

            await drain_next_queued_message(ctx, receipt.lane)
        return
    acknowledged = (
        receipt.status == "accepted"
        and not automatic
        and receipt.execution_status in (None, "inProgress")
    )
    if not acknowledged and (
        receipt.status != "ambiguous"
        or (automatic and receipt.reconciliation_attempts >= MAX_CHECKS)
    ):
        return
    # Charge the read before I/O, so crashes cannot replenish the finite budget.
    if not acknowledged:
        check = await ctx.registry.note_delivery_check(
            delivery_id,
            "provider history check in progress",
            max_attempts=MAX_CHECKS if automatic else None,
        )
        if check is None:
            return
    expected = json.loads(receipt.payload)["text"]
    try:
        async with asyncio.timeout(8):
            if receipt.transport == "native_queue":
                from .native_queue_evidence import NativeQueueConflict, find_queued_submission

                try:
                    submission = await find_queued_submission(
                        ctx, receipt.lane, receipt.id, expected
                    )
                except NativeQueueConflict:
                    raise
                except (ClientError, TimeoutError) as exc:
                    ctx.log.warning(
                        "delivery.native_queue_check_failed", delivery_id=receipt.id, error=str(exc)
                    )
                    submission = None
                if submission is not None:
                    await ctx.registry.apply_receipt_observation(
                        ProviderObservation(
                            provider=receipt.provider,
                            binding_id=receipt.binding_id,
                            native_session_id=receipt.native_session_id or receipt.lane,
                            kind="accepted",
                            correlation=ProviderCorrelation(
                                delivery_id=receipt.id,
                                correlation_id=receipt.correlation_id,
                                native_submission_id=submission.id,
                            ),
                            generation=route.availability.generation,
                            source="history",
                            received_at=datetime.fromisoformat(ctx.registry.now_iso()),
                        )
                    )
                    return
            turn, reason = await _find_arrival(ctx, route, receipt.id, expected)
    except (ClientError, TimeoutError) as exc:
        turn, reason = None, f"provider history unavailable: {exc}"
    if turn is None:
        if acknowledged:
            await ctx.registry.update_delivery(
                delivery_id,
                status="accepted",
                error=(
                    f"request accepted; execution unverified: {reason}; "
                    "inspect history or reconcile again"
                )[:2000],
            )
            if receipt.transport == "turn":
                await _refresh_idle_readiness(receipt.lane, ctx)
            return
        checked = await ctx.registry.get_delivery(delivery_id)
        attention = (
            "automatic checks exhausted; " if checked.reconciliation_attempts >= MAX_CHECKS else ""
        )
        await ctx.registry.apply_receipt_observation(
            ProviderObservation(
                provider=receipt.provider,
                binding_id=receipt.binding_id,
                native_session_id=receipt.native_session_id or receipt.lane,
                kind="uncertain",
                correlation=ProviderCorrelation(
                    delivery_id=receipt.id, correlation_id=receipt.correlation_id
                ),
                generation=route.availability.generation,
                source="history",
                received_at=datetime.fromisoformat(ctx.registry.now_iso()),
                partial=True,
                reason=(
                    f"{reason}; {attention}delivery remains held; inspect provider history and "
                    f"run dispatch delivery reconcile {delivery_id}; do not resend"
                )[:2000],
            )
        )
        if attention:
            ctx.log.warning("delivery.needs_attention", delivery_id=delivery_id, lane=receipt.lane)
        return
    await ctx.registry.apply_receipt_observation(
        ProviderObservation(
            provider=receipt.provider,
            binding_id=receipt.binding_id,
            native_session_id=receipt.native_session_id or receipt.lane,
            kind="started" if turn.status == "inProgress" else turn.status,
            correlation=ProviderCorrelation(
                delivery_id=receipt.id,
                correlation_id=receipt.correlation_id,
                native_submission_id=receipt.submission_id,
                native_run_id=turn.id,
            ),
            generation=route.availability.generation,
            source="history",
            received_at=datetime.fromisoformat(ctx.registry.now_iso()),
            reason=turn.error.message if turn.error is not None else None,
        )
    )
    from .delivery import observe_delivery_execution

    await observe_delivery_execution(receipt.lane, turn.id, ctx)
    if turn.status == "completed" and receipt.transport == "turn":
        ready = await _refresh_idle_readiness(receipt.lane, ctx)
        if ready and not automatic:
            from .queue import drain_next_queued_message

            await drain_next_queued_message(ctx, receipt.lane)


async def _refresh_idle_readiness(lane_id: str, ctx: Ctx) -> bool:
    """A historical completion alone cannot establish current destination readiness."""
    try:
        async with asyncio.timeout(8):
            for _ in range(MAX_READINESS_CHECKS):
                lane = await ctx.registry.find_lane(lane_id)
                if lane is None or lane.status in ("archived", "error"):
                    return False
                route = _evidence_route(ctx, lane, native_queue=False)
                if route is None:
                    return False
                try:
                    route.recheck()
                    result = await route.adapter.read(route.target, include_turns=False)
                    route.recheck()
                except CapabilityUnavailableError:
                    return False
                except (ClientError, TimeoutError):
                    continue
                thread = result.get("thread")
                if (
                    not isinstance(thread, dict)
                    or thread.get("id") != route.target.native_session_id
                ):
                    continue
                status = thread.get("status")
                if not isinstance(status, dict):
                    continue
                readiness = ProviderObservation(
                    provider=route.target.provider,
                    binding_id=route.target.binding_id,
                    native_session_id=route.target.native_session_id,
                    kind="readiness",
                    correlation=ProviderCorrelation(),
                    generation=route.availability.generation,
                    source="read",
                    received_at=datetime.fromisoformat(ctx.registry.now_iso()),
                    readiness="ready" if status.get("type") == "idle" else "busy",
                )
                if readiness.readiness != "ready":
                    return False
                # A newer event winning the CAS ends this check; it is not
                # permission to immediately overwrite that event on a retry.
                return await ctx.registry.reconcile_lane_idle(
                    lane_id,
                    lane.updated_at,
                    expected_status=lane.status,
                    expected_active_turn_id=lane.active_turn_id,
                )
    except TimeoutError:
        pass
    return False


async def _find_arrival(
    ctx: Ctx,
    route: ProviderRoute,
    delivery_id: str,
    expected: str,
) -> tuple[ThreadTurn | None, str]:
    cursor: str | None = None
    seen: set[str] = set()
    matches: list[ThreadTurn] = []
    mismatch = False
    total_bytes = 0
    for _ in range(MAX_PAGES):
        route.recheck()
        page = await route.adapter.turns_list(
            route.target,
            cursor=cursor,
            limit=50,
            sort_direction="desc",
            items_view="full",
        )
        total_bytes += len(page.model_dump_json().encode())
        if total_bytes > MAX_HISTORY_BYTES:
            return None, "history byte budget exhausted (incomplete)"
        for turn in page.data:
            if turn.items_view != "full":
                return None, "history omitted full items (incomplete)"
            for item in turn.items:
                if item.get("type") != "userMessage" or item.get("clientId") != delivery_id:
                    continue
                content = item.get("content")
                exact = (
                    isinstance(content, list)
                    and len(content) == 1
                    and isinstance(content[0], dict)
                    and content[0].get("type") == "text"
                    and content[0].get("text") == expected
                )
                mismatch |= not exact
                matches.append(turn)
        cursor = page.next_cursor
        if cursor is None:
            if mismatch or len(matches) > 1:
                return None, "conflicting or duplicate provider delivery ID"
            if len(matches) == 1 and matches[0].id:
                return matches[0], "exact provider user message"
            return None, "delivery not visible in this history read (inconclusive)"
        if cursor in seen:
            return None, "history cursor repeated (incomplete)"
        seen.add(cursor)
    return None, "history page budget exhausted (incomplete)"
