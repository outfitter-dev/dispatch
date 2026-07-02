#!/usr/bin/env python
"""Measure synthetic provider-event ingestion through the Dispatch registry."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from outfitter.dispatch.registry.ingest_harness import (
    EventIngestionHarnessConfig,
    run_event_ingestion_harness,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure synthetic Dispatch provider-event ingestion.",
    )
    parser.add_argument("--events", type=int, default=100, help="Synthetic events to write.")
    parser.add_argument("--lanes", type=int, default=4, help="Synthetic lanes to create.")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrent writer tasks.")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Registry database path. Defaults to a temporary synthetic database.",
    )
    parser.add_argument(
        "--reader",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Sample history-summary reads while writes are in flight.",
    )
    parser.add_argument(
        "--raw-retained",
        action="store_true",
        help="Mark synthetic payloads as raw-retained for retention-shape measurements.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        result = asyncio.run(
            run_event_ingestion_harness(
                EventIngestionHarnessConfig(
                    events=args.events,
                    lanes=args.lanes,
                    concurrency=args.concurrency,
                    db_path=args.db,
                    reader=args.reader,
                    raw_retained=args.raw_retained,
                )
            )
        )
    except ValueError as exc:
        parser.error(str(exc))

    data = result.as_dict()
    if args.json:
        print(json.dumps(data, sort_keys=True))
    else:
        print(f"db: {data['db_path']}")
        print(f"temporary_db: {data['temporary_db']}")
        print(
            f"events: {data['totals']['provider_events']} / {data['events_requested']} "
            f"at {data['events_per_second']} events/s"
        )
        print(f"lanes: {data['lanes']}; concurrency: {data['concurrency']}")
        print(f"reader_samples: {data['reader_samples']}")
        print(f"totals: {json.dumps(data['totals'], sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
