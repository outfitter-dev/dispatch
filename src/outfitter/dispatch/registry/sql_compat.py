"""Registry SQL compatibility probes.

These checks exercise the SQL shapes Dispatch relies on most heavily without
introducing a second registry implementation. They are intentionally synchronous
so they can run against sqlite-like DB-API connections from stdlib sqlite,
libSQL, or Turso client packages.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from .store import REGISTRY_SCHEMA_SQL, SCHEMA_VERSION

Connect = Callable[[str], Any]


@dataclass(frozen=True)
class SqlCompatibilityResult:
    backend: str
    status: str
    elapsed_ms: float
    detail: str


def run_sql_compat_probe(backend: str, connect: Connect) -> SqlCompatibilityResult:
    """Run the representative registry SQL compatibility probe for a backend."""

    started = perf_counter()
    with TemporaryDirectory(prefix=f"dispatch-{backend}-") as tmp:
        path = str(Path(tmp) / "registry.db")
        try:
            conn = connect(path)
            try:
                exercise_registry_sql(conn)
            finally:
                close_connection(conn)
        except Exception as exc:
            return SqlCompatibilityResult(
                backend,
                "FAIL",
                (perf_counter() - started) * 1000,
                f"{type(exc).__name__}: {exc}",
            )
    partial_target = partial_conflict_target_supported(connect)
    return SqlCompatibilityResult(
        backend,
        "PASS",
        (perf_counter() - started) * 1000,
        "schema, upsert, transaction rollback, and summary query worked; "
        f"partial conflict target supported={partial_target}",
    )


def exercise_registry_sql(conn: Any) -> None:
    """Exercise schema install plus representative writes and reads."""

    execute(conn, "PRAGMA foreign_keys = ON")
    execute(conn, "PRAGMA busy_timeout = 5000")
    executescript(conn, REGISTRY_SCHEMA_SQL)
    execute(conn, f"PRAGMA user_version = {SCHEMA_VERSION}")
    commit(conn)

    execute(
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
    execute(
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
    execute(
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
    execute(
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
    execute(
        conn,
        """
        INSERT OR IGNORE INTO thread_item_refs (
            provider, provider_thread_id, item_id, ref_type, ref_value
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("codex", "thread-1", "item-1", "tool", "bash"),
    )
    commit(conn)

    rows = fetchall(
        conn,
        """
        SELECT COUNT(*) AS items, SUM(length(COALESCE(text, ''))) AS transcript_bytes
        FROM thread_items
        WHERE lane = ?
        """,
        ("L1",),
    )
    if first_value(rows[0]) != 1:
        raise AssertionError(f"expected one thread item, got {rows!r}")

    execute(conn, "BEGIN")
    execute(
        conn,
        "INSERT INTO actions_log (ts, op, lane, outcome) VALUES (?, ?, ?, ?)",
        ("2026-07-02T12:00:03+00:00", "probe", "L1", "ok"),
    )
    rollback(conn)
    rows = fetchall(conn, "SELECT COUNT(*) FROM actions_log WHERE op = ?", ("probe",))
    if first_value(rows[0]) != 0:
        raise AssertionError("transaction rollback did not discard actions_log row")


def partial_conflict_target_supported(connect: Connect) -> bool:
    """Return whether a backend supports SQLite partial-index conflict targets."""

    try:
        conn = connect(":memory:")
        try:
            execute(conn, "CREATE TABLE events(provider TEXT NOT NULL, event_id TEXT)")
            execute(
                conn,
                "CREATE UNIQUE INDEX idx_events ON events(provider, event_id) "
                "WHERE event_id IS NOT NULL",
            )
            execute(
                conn,
                "INSERT INTO events(provider, event_id) VALUES (?, ?) "
                "ON CONFLICT(provider, event_id) WHERE event_id IS NOT NULL DO NOTHING",
                ("codex", "event-1"),
            )
            commit(conn)
            return True
        finally:
            close_connection(conn)
    except Exception:
        return False


def execute(conn: Any, sql: str, params: tuple[object, ...] = ()) -> Any:
    return conn.execute(sql, params) if params else conn.execute(sql)


def executescript(conn: Any, sql: str) -> None:
    script = getattr(conn, "executescript", None)
    if callable(script):
        script(sql)
        return
    with closing(conn.cursor()) as cursor:
        cursor.executescript(sql)


def fetchall(conn: Any, sql: str, params: tuple[object, ...] = ()) -> list[Any]:
    cursor = execute(conn, sql, params)
    try:
        return list(cursor.fetchall())
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def commit(conn: Any) -> None:
    commit_method = getattr(conn, "commit", None)
    if callable(commit_method):
        commit_method()


def rollback(conn: Any) -> None:
    rollback_method = getattr(conn, "rollback", None)
    if callable(rollback_method):
        rollback_method()


def first_value(row: Any) -> object:
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def close_connection(conn: Any) -> None:
    close = getattr(conn, "close", None)
    if callable(close):
        close()
