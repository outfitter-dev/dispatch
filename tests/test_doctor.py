"""Install and runtime diagnostics."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from outfitter.dispatch.cli import app as cli_app
from outfitter.dispatch.doctor import DoctorOptions, run_doctor
from outfitter.dispatch.registry.store import SCHEMA_VERSION, Registry
from outfitter.dispatch.surfaces.cli import build_cli

runner = CliRunner()


def _write_fake_codex(path: Path, version: str) -> None:
    path.write_text(f"#!/bin/sh\necho 'codex-cli {version}'\n")
    path.chmod(0o755)


def _create_v3_registry(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE lanes (
                id TEXT PRIMARY KEY,
                ref TEXT NOT NULL UNIQUE,
                ref_source TEXT NOT NULL,
                ref_payload TEXT NOT NULL,
                ref_mixer TEXT NOT NULL,
                handle TEXT NOT NULL,
                role TEXT,
                cwd TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unknown',
                pinned INTEGER NOT NULL DEFAULT 0,
                active_turn_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_event_at TEXT
            );
            INSERT INTO lanes (
                id, ref, ref_source, ref_payload, ref_mixer, handle, source, status,
                pinned, created_at, updated_at
            ) VALUES (
                'A', '0abc1', '0', 'abc', '1', '@a', 'own', 'idle', 0,
                '2026-06-03T12:00:01+00:00', '2026-06-03T12:00:01+00:00'
            );
            CREATE TABLE triggers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                lane_selector TEXT NOT NULL,
                when_spec TEXT NOT NULL,
                action_spec TEXT NOT NULL,
                guard_spec TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT,
                last_fired_at TEXT
            );
            CREATE TABLE actions_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                op TEXT NOT NULL,
                lane TEXT,
                trigger_id TEXT,
                detail TEXT,
                outcome TEXT NOT NULL DEFAULT 'ok'
            );
            CREATE TABLE queued_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lane TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE lane_sync_sources (
                lane TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                source_path TEXT,
                source_device INTEGER,
                source_inode INTEGER,
                source_size INTEGER,
                source_mtime_ns INTEGER,
                line_count INTEGER,
                first_offset INTEGER,
                tail_offset INTEGER,
                last_synced_at TEXT,
                error TEXT
            );
            CREATE TABLE lane_snapshots (
                lane TEXT PRIMARY KEY,
                display_name TEXT,
                preview TEXT,
                cwd TEXT,
                source TEXT,
                thread_source TEXT,
                model_provider TEXT,
                model TEXT,
                reasoning_effort TEXT,
                session_id TEXT,
                latest_event_at TEXT,
                latest_turn_id TEXT,
                transcript_partial INTEGER NOT NULL DEFAULT 1
            );
            PRAGMA user_version = 3;
            """
        )


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


def test_doctor_warns_when_resolved_codex_is_below_supported_floor(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "codex"
    _write_fake_codex(binary, "0.146.0")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    report = run_doctor(DoctorOptions(app_server=False))

    check = next(item for item in report.checks if item.name == "codex_binary")
    assert check.status == "warn"
    assert check.data == {
        "path": str(binary),
        "version": "0.146.0",
        "minimum_version": "0.147.0",
    }
    assert "0.146.0" in check.summary
    assert "0.147.0" in check.summary
    assert check.detail == f"Resolved binary: {binary}"
    assert check.recovery is not None and "Update Codex CLI" in check.recovery


def test_doctor_structures_invalid_compatibility_manifest(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "codex"
    manifest = tmp_path / "protocol_manifest.json"
    _write_fake_codex(binary, "0.147.0")
    manifest.write_text("{}")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr("outfitter.dispatch.codex_compat._manifest_path", lambda: manifest)

    report = run_doctor(DoctorOptions(app_server=False))

    check = next(item for item in report.checks if item.name == "codex_binary")
    assert check.status == "fail"
    assert check.detail == "protocol manifest is missing minimum_codex_cli_version"


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


def test_doctor_reports_capture_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    report = run_doctor(DoctorOptions(app_server=False))

    capture = next(check for check in report.checks if check.name == "capture_policy")
    assert capture.status == "ok"
    assert capture.summary == "history capture mode is standard"
    assert capture.data["mode"] == "standard"
    assert capture.data["raw_payloads_enabled"] is False


def test_doctor_warns_for_debug_capture(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DISPATCH_CAPTURE", "debug")

    report = run_doctor(DoctorOptions(app_server=False))

    capture = next(check for check in report.checks if check.name == "capture_policy")
    assert capture.status == "warn"
    assert capture.summary == "history capture debug/raw retention is enabled"
    assert capture.data["mode"] == "debug"
    assert capture.data["raw_payloads_enabled"] is True
    assert capture.data["retains_any_raw_payloads"] is True


def test_doctor_warns_for_error_raw_retention(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DISPATCH_RAW_PAYLOAD_RETENTION", "errors")

    report = run_doctor(DoctorOptions(app_server=False))

    capture = next(check for check in report.checks if check.name == "capture_policy")
    assert capture.status == "warn"
    assert capture.data["raw_payload_retention"] == "errors"
    assert capture.data["raw_payloads_enabled"] is True
    assert capture.data["retains_any_raw_payloads"] is True


def test_doctor_fails_for_invalid_capture_cap(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DISPATCH_CAPTURE_MAX_PAYLOAD_BYTES", "0")

    report = run_doctor(DoctorOptions(app_server=False))

    capture = next(check for check in report.checks if check.name == "capture_policy")
    assert capture.status == "fail"
    assert capture.summary == "history capture policy is invalid"
    assert capture.detail is not None
    assert "history.max_payload_bytes" in capture.detail


def test_doctor_fails_for_relative_shared_app_server_socket(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DISPATCH_APP_SERVER_SOCKET", "relative.sock")

    report = run_doctor()

    app_server = next(check for check in report.checks if check.name == "app_server")
    assert app_server.status == "fail"
    assert app_server.summary == "shared app-server configuration is invalid"
    assert app_server.detail is not None
    assert "absolute path" in app_server.detail


def test_doctor_reports_unreadable_shared_socket_config(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "config-as-directory"
    config_dir.mkdir()
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DISPATCH_CONFIG", str(config_dir))

    report = run_doctor()

    app_server = next(check for check in report.checks if check.name == "app_server")
    assert app_server.status == "fail"
    assert app_server.summary == "shared app-server configuration is invalid"
    assert app_server.detail is not None
    assert app_server.recovery is not None


def test_doctor_reports_malformed_shared_socket_config(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("[app_server\nsocket_path = broken")
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DISPATCH_CONFIG", str(config_file))

    report = run_doctor()

    app_server = next(check for check in report.checks if check.name == "app_server")
    assert app_server.status == "fail"
    assert app_server.summary == "shared app-server configuration is invalid"
    assert app_server.detail is not None


def test_doctor_downgrades_missing_codex_when_shared_socket_configured(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DISPATCH_APP_SERVER_SOCKET", str(tmp_path / "app-server.sock"))

    report = run_doctor(DoctorOptions(app_server=False))

    checks = {check.name: check for check in report.checks}
    assert checks["codex_binary"].status == "warn"
    assert checks["codex_binary"].recovery is not None
    assert "socket_path" in checks["codex_binary"].recovery
    assert report.status == "warn"


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
    assert registry.detail == (
        "missing tables: lane_model_settings, lane_runtime_settings, lane_snapshots, "
        "lane_sync_sources, model_catalog, queued_messages"
    )
    assert registry.recovery is not None
    assert "dispatch down" in registry.recovery


def test_doctor_warns_for_old_registry_migration(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "dispatch-home"
    home.mkdir()
    _create_v3_registry(home / "registry.db")
    monkeypatch.setenv("DISPATCH_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    report = run_doctor(DoctorOptions(app_server=False))

    registry = next(check for check in report.checks if check.name == "registry")
    assert registry.status == "warn"
    assert registry.summary == "registry schema is older than this dispatch binary supports"
    assert registry.recovery is not None
    assert "dispatch registry migrate" in registry.recovery
    assert registry.data["schema_version"] == 3
    assert registry.data["supported_schema_version"] == SCHEMA_VERSION
    assert registry.data["row_counts"] == {"lanes": 1, "triggers": 0}


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


def test_up_down_support_json(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "dispatch-home"))

    def start_detached(_socket: Path, _pidfile: Path) -> bool:
        return True

    def stop_daemon(_socket: Path, _pidfile: Path) -> bool:
        return True

    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.start_detached", start_detached)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop_daemon)
    app = build_cli(socket_path=tmp_path / "dispatchd.sock")

    up = runner.invoke(app, ["up", "--json"])
    down = runner.invoke(app, ["down", "--json"])

    assert up.exit_code == 0
    assert down.exit_code == 0
    assert '"status": "started"' in up.output
    assert '"started": true' in up.output
    assert '"status": "stopped"' in down.output
    assert '"stopped": true' in down.output


def test_registry_migrate_command_updates_old_schema(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "dispatch-home"
    home.mkdir()
    _create_v3_registry(home / "registry.db")
    monkeypatch.setenv("DISPATCH_HOME", str(home))
    app = build_cli(socket_path=tmp_path / "dispatchd.sock")

    result = runner.invoke(app, ["registry", "migrate", "--json", "--no-backup"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["from_schema_version"] == 3
    assert payload["to_schema_version"] == SCHEMA_VERSION
    assert payload["migrated"] is True
    with sqlite3.connect(home / "registry.db") as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(lanes)").fetchall()}
    assert version == SCHEMA_VERSION
    assert {"latest_turn_id", "latest_turn_status", "latest_error", "latest_error_at"} <= columns


def test_registry_migrate_blocks_while_daemon_running(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "dispatch-home"
    home.mkdir()
    _create_v3_registry(home / "registry.db")
    monkeypatch.setenv("DISPATCH_HOME", str(home))
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.is_daemon_up", lambda _path: True)
    app = build_cli(socket_path=tmp_path / "dispatchd.sock")

    result = runner.invoke(app, ["registry", "migrate", "--json", "--no-backup"])

    assert result.exit_code == 8
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["reason"] == "daemon_running"
    assert "dispatch down" in payload["recovery"]
