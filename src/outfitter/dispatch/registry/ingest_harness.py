"""Synthetic provider-event ingestion harness.

The harness is opt-in and uses generated data only. It measures the current
Registry write path before Dispatch changes storage engines or transaction
shape.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

from .models import (
    LaneRuntimeState,
    MessageReceipt,
    ProviderEvent,
    ThreadItem,
    ThreadItemRef,
    ThreadTurn,
)
from .store import Registry


@dataclass(frozen=True)
class EventIngestionHarnessConfig:
    events: int = 100
    lanes: int = 4
    concurrency: int = 4
    db_path: Path | None = None
    reader: bool = True
    raw_retained: bool = False


@dataclass(frozen=True)
class EventIngestionHarnessResult:
    db_path: str
    temporary_db: bool
    lanes: int
    events_requested: int
    concurrency: int
    reader_enabled: bool
    raw_retained: bool
    elapsed_ms: float
    events_per_second: float
    reader_samples: int
    totals: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "db_path": self.db_path,
            "temporary_db": self.temporary_db,
            "lanes": self.lanes,
            "events_requested": self.events_requested,
            "concurrency": self.concurrency,
            "reader_enabled": self.reader_enabled,
            "raw_retained": self.raw_retained,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "events_per_second": round(self.events_per_second, 3),
            "reader_samples": self.reader_samples,
            "totals": self.totals,
        }


async def run_event_ingestion_harness(
    config: EventIngestionHarnessConfig,
) -> EventIngestionHarnessResult:
    """Run a synthetic provider-history ingestion measurement."""

    _validate_config(config)
    if config.db_path is not None:
        return await _run_with_path(config, config.db_path, temporary_db=False)
    with TemporaryDirectory(prefix="dispatch-ingest-") as tmp:
        return await _run_with_path(config, Path(tmp) / "registry.sqlite3", temporary_db=True)


async def _run_with_path(
    config: EventIngestionHarnessConfig, db_path: Path, *, temporary_db: bool
) -> EventIngestionHarnessResult:
    run_id = uuid4().hex[:8]
    store = await Registry.open(db_path)
    try:
        lane_ids = [f"load-{run_id}-{index}" for index in range(config.lanes)]
        for index, lane_id in enumerate(lane_ids):
            await store.add_lane(
                id=lane_id,
                handle=f"@load-{index}",
                source="own",
                cwd="/tmp/dispatch-ingest-harness",
            )

        stop_reader = asyncio.Event()
        reader_samples = 0

        async def reader_loop() -> None:
            nonlocal reader_samples
            while not stop_reader.is_set():
                for lane_id in lane_ids:
                    await store.get_thread_history_summary_stats(lane=lane_id)
                reader_samples += 1
                await asyncio.sleep(0)

        reader_task = asyncio.create_task(reader_loop()) if config.reader else None
        started = perf_counter()
        try:
            await asyncio.gather(
                *(
                    _worker(store, config, lane_ids, worker_id)
                    for worker_id in range(config.concurrency)
                )
            )
        finally:
            stop_reader.set()
            if reader_task is not None:
                await reader_task
        elapsed_ms = (perf_counter() - started) * 1000
        totals = await _collect_totals(store, lane_ids, limit=config.events + config.lanes)
    finally:
        await store.close()

    elapsed_seconds = elapsed_ms / 1000
    events_per_second = totals["provider_events"] / elapsed_seconds if elapsed_seconds > 0 else 0.0
    return EventIngestionHarnessResult(
        db_path=str(db_path),
        temporary_db=temporary_db,
        lanes=config.lanes,
        events_requested=config.events,
        concurrency=config.concurrency,
        reader_enabled=config.reader,
        raw_retained=config.raw_retained,
        elapsed_ms=elapsed_ms,
        events_per_second=events_per_second,
        reader_samples=reader_samples,
        totals=totals,
    )


async def _worker(
    store: Registry,
    config: EventIngestionHarnessConfig,
    lane_ids: list[str],
    worker_id: int,
) -> None:
    for index in range(worker_id, config.events, config.concurrency):
        await _write_synthetic_event(store, config, lane_ids, index)


async def _write_synthetic_event(
    store: Registry,
    config: EventIngestionHarnessConfig,
    lane_ids: list[str],
    index: int,
) -> None:
    lane = lane_ids[index % len(lane_ids)]
    thread_id = f"thread-{lane}"
    turn_id = f"turn-{index:06d}"
    item_id = f"item-{index:06d}"
    timestamp = _timestamp(index)

    await store.record_provider_event(
        ProviderEvent(
            provider="codex",
            provider_thread_id=thread_id,
            lane=lane,
            event_type="turn/completed",
            provider_event_id=f"event-{index:06d}",
            provider_turn_id=turn_id,
            provider_item_id=item_id,
            provider_ts=timestamp,
            received_at=timestamp,
            summary={"status": "completed", "synthetic": True},
            payload={"method": "turn/completed", "index": index},
            raw_retained=config.raw_retained,
        )
    )
    await store.upsert_thread_turn(
        ThreadTurn(
            provider="codex",
            provider_thread_id=thread_id,
            turn_id=turn_id,
            lane=lane,
            status="completed",
            started_at=timestamp,
            completed_at=timestamp,
            completion_source="synthetic-harness",
            updated_at=timestamp,
        )
    )
    await store.upsert_thread_item(
        ThreadItem(
            provider="codex",
            provider_thread_id=thread_id,
            item_id=item_id,
            lane=lane,
            turn_id=turn_id,
            item_type="toolCall",
            role="assistant",
            text=f"synthetic command {index}",
            tool="bash",
            created_at=timestamp,
            position=index,
            inserted_at=timestamp,
            payload={"type": "toolCall", "index": index},
            raw_retained=config.raw_retained,
        ),
        refs=[
            ThreadItemRef(
                provider="codex",
                provider_thread_id=thread_id,
                item_id=item_id,
                ref_type="file",
                ref_value=f"src/synthetic/{index % 10}.py",
            )
        ],
    )
    await store.upsert_message_receipt(
        MessageReceipt(
            lane=lane,
            provider="codex",
            provider_thread_id=thread_id,
            dispatch_message_id=f"dispatch-message-{index:06d}",
            status="completed",
            turn_id=turn_id,
            created_at=timestamp,
            sent_at=timestamp,
            accepted_at=timestamp,
            completed_at=timestamp,
            updated_at=timestamp,
        )
    )
    await store.upsert_lane_runtime_state(
        LaneRuntimeState(
            lane=lane,
            provider="codex",
            provider_thread_id=thread_id,
            status="idle",
            latest_turn_id=turn_id,
            latest_turn_status="completed",
            updated_at=timestamp,
            last_event_at=timestamp,
        )
    )


async def _collect_totals(store: Registry, lane_ids: list[str], *, limit: int) -> dict[str, int]:
    provider_events = 0
    thread_turns = 0
    thread_items = 0
    transcript_bytes = 0
    message_receipts = 0
    for lane in lane_ids:
        provider_events += len(await store.list_provider_events(lane=lane, limit=limit))
        stats = await store.get_thread_history_summary_stats(lane=lane)
        thread_turns += stats.turns
        thread_items += stats.items
        transcript_bytes += stats.transcript_bytes or 0
        message_receipts += len(await store.list_message_receipts(lane=lane, limit=limit))
    return {
        "provider_events": provider_events,
        "thread_turns": thread_turns,
        "thread_items": thread_items,
        "message_receipts": message_receipts,
        "transcript_bytes": transcript_bytes,
    }


def _validate_config(config: EventIngestionHarnessConfig) -> None:
    if config.events < 1:
        raise ValueError("events must be at least 1")
    if config.lanes < 1:
        raise ValueError("lanes must be at least 1")
    if config.concurrency < 1:
        raise ValueError("concurrency must be at least 1")


def _timestamp(index: int) -> str:
    return (datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=index)).isoformat()
