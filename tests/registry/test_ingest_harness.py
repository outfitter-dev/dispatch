"""Synthetic event-ingestion harness tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from outfitter.dispatch.registry.ingest_harness import (
    EventIngestionHarnessConfig,
    _timestamp,
    run_event_ingestion_harness,
)


@pytest.mark.asyncio
async def test_event_ingestion_harness_writes_expected_registry_shapes(
    tmp_path: Path,
) -> None:
    result = await run_event_ingestion_harness(
        EventIngestionHarnessConfig(
            db_path=tmp_path / "registry.sqlite3",
            events=12,
            lanes=3,
            concurrency=4,
        )
    )

    assert result.temporary_db is False
    assert result.lanes == 3
    assert result.events_requested == 12
    assert result.concurrency == 4
    assert result.totals["provider_events"] == 12
    assert result.totals["thread_turns"] == 12
    assert result.totals["thread_items"] == 12
    assert result.totals["message_receipts"] == 12
    assert result.totals["transcript_bytes"] > 0
    assert result.reader_samples >= 0
    assert result.events_per_second > 0


def test_measure_event_ingestion_script_emits_json(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    db = tmp_path / "script.sqlite3"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/measure_event_ingestion.py",
            "--db",
            str(db),
            "--events",
            "6",
            "--lanes",
            "2",
            "--concurrency",
            "3",
            "--no-reader",
            "--json",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    data = json.loads(completed.stdout)
    assert data["db_path"] == str(db)
    assert data["temporary_db"] is False
    assert data["reader_samples"] == 0
    assert data["totals"]["provider_events"] == 6
    assert data["totals"]["thread_items"] == 6


def test_event_ingestion_timestamp_handles_longer_runs() -> None:
    assert _timestamp(3_700) == "2026-07-02T13:01:40+00:00"
