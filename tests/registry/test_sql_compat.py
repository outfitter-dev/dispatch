"""Contract tests for representative registry SQL compatibility."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from outfitter.dispatch.registry.sql_compat import (
    exercise_registry_sql,
    partial_conflict_target_supported,
    run_sql_compat_probe,
)
from outfitter.dispatch.registry.store import SCHEMA_VERSION


def test_sql_compat_probe_passes_for_stdlib_sqlite() -> None:
    result = run_sql_compat_probe("sqlite3", lambda path: sqlite3.connect(path))

    assert result.backend == "sqlite3"
    assert result.status == "PASS"
    assert result.elapsed_ms >= 0
    assert "schema, upsert, transaction rollback" in result.detail


def test_registry_sql_exercise_sets_schema_version_and_rolls_back(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "registry.sqlite3")
    try:
        exercise_registry_sql(conn)

        user_version = conn.execute("PRAGMA user_version").fetchone()
        assert user_version is not None
        assert int(user_version[0]) == SCHEMA_VERSION

        server_requests = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("server_requests",),
        ).fetchone()
        assert server_requests is not None

        provider_thread_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(provider_threads)").fetchall()
        }
        assert {
            "provider",
            "provider_thread_id",
            "parent_thread_id",
            "forked_from_id",
            "lifecycle_state",
            "first_seen_at",
            "last_seen_at",
        } <= provider_thread_columns
        assert conn.execute("PRAGMA foreign_key_list(provider_threads)").fetchall() == []
        conn.execute(
            "INSERT INTO provider_threads (provider, provider_thread_id, parent_thread_id, "
            "lifecycle_state, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, provider_thread_id) DO UPDATE SET "
            "parent_thread_id = COALESCE(excluded.parent_thread_id, "
            "provider_threads.parent_thread_id), last_seen_at = excluded.last_seen_at",
            (
                "codex",
                "thread-topology",
                "parent-topology",
                "active",
                "2026-07-02T12:00:00+00:00",
                "2026-07-02T12:00:01+00:00",
            ),
        )
        conn.commit()

        provider_capacity_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(provider_capacity_observations)").fetchall()
        }
        assert {
            "provider",
            "host_scope",
            "config_scope",
            "state",
            "account_fingerprint",
            "observed_at",
            "payload",
        } <= provider_capacity_columns

        thread_item_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(thread_items)").fetchall()
        }
        assert {
            "phase",
            "status",
            "server",
            "command",
            "cwd",
            "error",
            "duration_ms",
            "arguments",
            "success",
            "agent_nickname",
            "agent_role",
        } <= thread_item_columns

        rolled_back = conn.execute(
            "SELECT COUNT(*) FROM actions_log WHERE op = ?",
            ("probe",),
        ).fetchone()
        assert rolled_back is not None
        assert int(rolled_back[0]) == 0
    finally:
        conn.close()


def test_partial_conflict_target_supported_for_stdlib_sqlite() -> None:
    assert partial_conflict_target_supported(lambda path: sqlite3.connect(path))
