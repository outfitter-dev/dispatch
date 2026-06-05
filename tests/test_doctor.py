"""Install and runtime diagnostics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from outfitter.dispatch.cli import app as cli_app
from outfitter.dispatch.doctor import DoctorOptions, run_doctor
from outfitter.dispatch.registry.store import SCHEMA_VERSION, Registry

runner = CliRunner()


def test_doctor_reports_missing_console_scripts_and_skips_app_server(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    report = run_doctor(DoctorOptions(app_server=False))

    checks = {check.name: check for check in report.checks}
    assert report.status in {"warn", "fail"}
    assert checks["path"].status == "warn"
    assert checks["codex_binary"].status == "fail"
    assert checks["app_server"].status == "warn"
    assert checks["app_server"].recovery is not None


def test_doctor_warns_for_stale_daemon_files(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "dispatch-home"
    home.mkdir()
    (home / "dispatchd.sock").write_text("")
    (home / "dispatchd.pid").write_text("123456")
    monkeypatch.setenv("DISPATCH_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    report = run_doctor(DoctorOptions(app_server=False))

    daemon = next(check for check in report.checks if check.name == "daemon")
    assert daemon.status == "warn"
    assert "stale" in daemon.summary
    assert daemon.recovery is not None
    assert "dispatch down" in daemon.recovery


def test_doctor_warns_for_unversioned_registry_migration(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "dispatch-home"
    home.mkdir()
    with sqlite3.connect(home / "registry.db") as conn:
        conn.executescript(
            """
            CREATE TABLE lanes (id TEXT);
            CREATE TABLE triggers (id TEXT);
            CREATE TABLE actions_log (id INTEGER);
            """
        )
    monkeypatch.setenv("DISPATCH_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    report = run_doctor(DoctorOptions(app_server=False))

    registry = next(check for check in report.checks if check.name == "registry")
    assert registry.status == "warn"
    assert registry.summary == "registry schema is unversioned"
    assert registry.detail == "missing tables: queued_messages"
    assert registry.recovery is not None
    assert "dispatch down" in registry.recovery


async def test_registry_open_marks_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"

    store = await Registry.open(path)
    await store.close()

    with sqlite3.connect(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION


async def test_registry_open_rejects_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

    try:
        await Registry.open(path)
    except RuntimeError as exc:
        assert "newer than supported" in str(exc)
    else:
        raise AssertionError("expected Registry.open to reject a newer schema")


def test_doctor_cli_json_and_text_modes(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    json_result = runner.invoke(cli_app, ["doctor", "--no-app-server"])
    text_result = runner.invoke(cli_app, ["doctor", "--no-app-server", "--text"])

    assert json_result.exit_code in {0, 8}
    assert '"checks"' in json_result.output
    assert text_result.exit_code in {0, 8}
    assert "dispatch doctor" in text_result.output
