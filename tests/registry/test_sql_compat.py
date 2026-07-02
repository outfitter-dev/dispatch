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
