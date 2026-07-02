"""CLI daemon/client skew recovery behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from outfitter.dispatch.surfaces import cli


def test_invoke_daemon_restarts_idle_stale_daemon_and_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    stopped: list[tuple[Path, Path]] = []
    started: list[tuple[Path, Path]] = []

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if calls == ["query"]:
            return {"id": 1, "error": {"code": -32601, "message": "unknown op 'query'"}}
        if method == "status":
            return {
                "id": 1,
                "result": {"lanes": 1, "idle": 1, "busy": 0, "active": 0},
            }
        return {"id": 1, "result": {"ok": True}}

    def stop(socket_path: Path, pidfile: Path) -> bool:
        stopped.append((socket_path, pidfile))
        return True

    def start(socket_path: Path, pidfile: Path) -> bool:
        started.append((socket_path, pidfile))
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.start_detached", start)

    result = cli.invoke_daemon(tmp_path / "dispatchd.sock", frozenset({"query"}), "query", {})

    assert result == {"ok": True}
    assert calls == ["query", "status", "query"]
    assert len(stopped) == 1
    assert len(started) == 1


def test_invoke_daemon_does_not_restart_busy_stale_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped = False

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        if method == "status":
            return {
                "id": 1,
                "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1},
            }
        return {"id": 1, "error": {"code": -32601, "message": "unknown op 'query'"}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        nonlocal stopped
        stopped = True
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)

    with pytest.raises(typer.Exit) as exc:
        cli.invoke_daemon(tmp_path / "dispatchd.sock", frozenset({"query"}), "query", {})

    assert exc.value.exit_code == 8
    assert stopped is False
    assert "not restarting automatically" in capsys.readouterr().err


def test_invoke_daemon_retries_stale_daemon_only_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    starts = 0

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        if method == "status":
            return {
                "id": 1,
                "result": {"lanes": 0, "idle": 0, "busy": 0, "active": 0},
            }
        return {"id": 1, "error": {"code": -32601, "message": "unknown op 'query'"}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        return True

    def start(_socket_path: Path, _pidfile: Path) -> bool:
        nonlocal starts
        starts += 1
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.start_detached", start)

    with pytest.raises(typer.Exit) as exc:
        cli.invoke_daemon(tmp_path / "dispatchd.sock", frozenset({"query"}), "query", {})

    assert exc.value.exit_code == 1
    assert starts == 1


def test_invoke_daemon_reports_restart_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        if method == "status":
            return {
                "id": 1,
                "result": {"lanes": 0, "idle": 0, "busy": 0, "active": 0},
            }
        return {"id": 1, "error": {"code": -32601, "message": "unknown op 'query'"}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        return True

    def start(_socket_path: Path, _pidfile: Path) -> bool:
        raise TimeoutError("daemon did not come up")

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.start_detached", start)

    with pytest.raises(typer.Exit) as exc:
        cli.invoke_daemon(tmp_path / "dispatchd.sock", frozenset({"query"}), "query", {})

    assert exc.value.exit_code == 8
    assert "restart failed" in capsys.readouterr().err


def test_invoke_daemon_does_not_treat_unknown_current_cli_typos_as_skew(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def request(_socket: Path, _method: str, _params: dict[str, object]) -> dict[str, object]:
        return {"id": 1, "error": {"code": -32601, "message": "unknown op 'typo'"}}

    monkeypatch.setattr(cli, "_control_request", request)

    with pytest.raises(typer.Exit) as exc:
        cli.invoke_daemon(tmp_path / "dispatchd.sock", frozenset({"query"}), "typo", {})

    assert exc.value.exit_code == 1
