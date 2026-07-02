"""Probe Dispatch registry SQL compatibility against Turso/libSQL packages.

This is an explicit spike, not production code. Run with:

    uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py

It answers a narrow question: can the current registry schema and representative
history writes run through sqlite-like Turso/libSQL DB-API connections without
touching Dispatch's default aiosqlite store?
"""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from outfitter.dispatch.registry.store import SCHEMA_VERSION, _SCHEMA


@dataclass(frozen=True)
class ProbeResult:
    backend: str
    status: str
    elapsed_ms: float
    detail: str


def main() -> None:
    results = [
        _run_backend("sqlite3", lambda path: sqlite3.connect(path)),
        _run_optional_backend("pyturso", "turso", lambda module, path: module.connect(path)),
        _run_optional_backend("libsql", "libsql", lambda module, path: module.connect(path)),
    ]
    width = max(len(result.backend) for result in results)
    for result in results:
        print(
            f"{result.backend:<{width}}  {result.status:<6}  "
            f"{result.elapsed_ms:8.2f} ms  {result.detail}"
        )
    if any(result.status == "FAIL" for result in results):
        raise SystemExit(1)


def _run_optional_backend(
    backend: str,
    module_name: str,
    connect: Callable[[Any, str], Any],
) -> ProbeResult:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return ProbeResult(backend, "SKIP", 0.0, f"install with --with {backend}")
    return _run_backend(backend, lambda path: connect(module, path))


def _run_backend(backend: str, connect: Callable[[str], Any]) -> ProbeResult:
    started = perf_counter()
    with TemporaryDirectory(prefix=f"dispatch-{backend}-") as tmp:
        path = str(Path(tmp) / "registry.db")
        try:
            conn = connect(path)
            try:
                _exercise(conn)
            finally:
                close = getattr(conn, "close", None)
                if callable(close):
                    close()
        except Exception as exc:  # noqa: BLE001 - spike should report exact backend failure.
            return ProbeResult(
                backend,
                "FAIL",
                (perf_counter() - started) * 1000,
                f"{type(exc).__name__}: {exc}",
            )
    partial_target = _partial_conflict_target_supported(connect)
    return ProbeResult(
        backend,
        "PASS",
        (perf_counter() - started) * 1000,
        "schema, upsert, transaction rollback, and summary query worked; "
        f"partial conflict target supported={partial_target}",
    )


def _exercise(conn: Any) -> None:
    _execute(conn, "PRAGMA foreign_keys = ON")
    _execute(conn, "PRAGMA busy_timeout = 5000")
    _executescript(conn, _SCHEMA)
    _execute(conn, f"PRAGMA user_version = {SCHEMA_VERSION}")
    _commit(conn)

    _execute(
        conn,
        """
        INSERT INTO lanes (
            id, ref, ref_source, ref_payload, ref_mixer, handle, source, status,
            pinned, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "L1",
            "0abc1234",
            "0",
            "abc1234",
            "0",
            "@probe",
            "own",
            "idle",
            0,
            "2026-07-02T12:00:00+00:00",
            "2026-07-02T12:00:00+00:00",
        ),
    )
    _execute(
        conn,
        """
        INSERT INTO provider_events (
            provider, provider_thread_id, lane, event_type, provider_event_id,
            provider_turn_id, received_at, summary, payload, raw_retained
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (
            "codex",
            "thread-1",
            "L1",
            "turn/started",
            "event-1",
            "turn-1",
            "2026-07-02T12:00:01+00:00",
            '{"status":"started"}',
            '{"method":"turn/started"}',
            1,
        ),
    )
    _execute(
        conn,
        """
        INSERT INTO thread_turns (
            provider, provider_thread_id, turn_id, lane, status, started_at,
            completion_source, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, provider_thread_id, turn_id) DO UPDATE SET
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        (
            "codex",
            "thread-1",
            "turn-1",
            "L1",
            "started",
            "2026-07-02T12:00:01+00:00",
            "probe",
            "2026-07-02T12:00:01+00:00",
        ),
    )
    _execute(
        conn,
        """
        INSERT INTO thread_items (
            provider, provider_thread_id, item_id, lane, turn_id, item_type,
            role, text, tool, created_at, position, inserted_at, payload, raw_retained
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, provider_thread_id, item_id) DO UPDATE SET
            text = excluded.text,
            position = excluded.position,
            payload = excluded.payload
        """,
        (
            "codex",
            "thread-1",
            "item-1",
            "L1",
            "turn-1",
            "toolCall",
            "assistant",
            "uv run pytest",
            "bash",
            "2026-07-02T12:00:02+00:00",
            1,
            "2026-07-02T12:00:02+00:00",
            '{"type":"toolCall","command":"uv run pytest"}',
            1,
        ),
    )
    _execute(
        conn,
        """
        INSERT OR IGNORE INTO thread_item_refs (
            provider, provider_thread_id, item_id, ref_type, ref_value
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("codex", "thread-1", "item-1", "tool", "bash"),
    )
    _commit(conn)

    rows = _fetchall(
        conn,
        """
        SELECT COUNT(*) AS items, SUM(length(COALESCE(text, ''))) AS transcript_bytes
        FROM thread_items
        WHERE lane = ?
        """,
        ("L1",),
    )
    if _first_value(rows[0]) != 1:
        raise AssertionError(f"expected one thread item, got {rows!r}")

    _execute(conn, "BEGIN")
    _execute(
        conn,
        "INSERT INTO actions_log (ts, op, lane, outcome) VALUES (?, ?, ?, ?)",
        ("2026-07-02T12:00:03+00:00", "probe", "L1", "ok"),
    )
    _rollback(conn)
    rows = _fetchall(conn, "SELECT COUNT(*) FROM actions_log WHERE op = ?", ("probe",))
    if _first_value(rows[0]) != 0:
        raise AssertionError("transaction rollback did not discard actions_log row")


def _execute(conn: Any, sql: str, params: tuple[object, ...] = ()) -> Any:
    return conn.execute(sql, params) if params else conn.execute(sql)


def _executescript(conn: Any, sql: str) -> None:
    script = getattr(conn, "executescript", None)
    if callable(script):
        script(sql)
        return
    with closing(conn.cursor()) as cursor:
        cursor.executescript(sql)


def _fetchall(conn: Any, sql: str, params: tuple[object, ...] = ()) -> list[Any]:
    cursor = _execute(conn, sql, params)
    try:
        return list(cursor.fetchall())
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def _commit(conn: Any) -> None:
    commit = getattr(conn, "commit", None)
    if callable(commit):
        commit()


def _rollback(conn: Any) -> None:
    rollback = getattr(conn, "rollback", None)
    if callable(rollback):
        rollback()


def _first_value(row: Any) -> object:
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _partial_conflict_target_supported(connect: Callable[[str], Any]) -> bool:
    try:
        conn = connect(":memory:")
        try:
            _execute(conn, "CREATE TABLE events(provider TEXT NOT NULL, event_id TEXT)")
            _execute(
                conn,
                "CREATE UNIQUE INDEX idx_events ON events(provider, event_id) "
                "WHERE event_id IS NOT NULL",
            )
            _execute(
                conn,
                "INSERT INTO events(provider, event_id) VALUES (?, ?) "
                "ON CONFLICT(provider, event_id) WHERE event_id IS NOT NULL DO NOTHING",
                ("codex", "event-1"),
            )
            _commit(conn)
            return True
        finally:
            close = getattr(conn, "close", None)
            if callable(close):
                close()
    except Exception:
        return False


if __name__ == "__main__":
    main()
