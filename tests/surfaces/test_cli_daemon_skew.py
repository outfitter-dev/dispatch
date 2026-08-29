"""CLI daemon/client skew recovery behavior.

Two staleness signals exist: the pre-flight op-schema handshake
(``__dispatch/metadata`` ``op_schemas``, one fingerprint per op), which catches
field-level drift a stale daemon would silently ignore (e.g. ``provider`` on
``new``), and the op-level method-not-found fallback for daemons replaced
mid-flight. The handshake gates per op: only the invoked op's fingerprint
matters, so hash-matching ops stay usable against a busy stale daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from outfitter.dispatch.contracts.legacy_baseline import PARENT_VERSION
from outfitter.dispatch.contracts.registry import (
    CONTROL_META_METHOD,
    registry_legacy_safe_ops,
    registry_read_safe_ops,
)
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.surfaces import cli

_LOCAL_HASHES: dict[str, str] = {
    "new": "hash-new",
    "new-plan": "hash-new-plan",
    "query": "hash-query",
    "roster": "hash-roster",
    "stop": "hash-stop",
}
_OPS = frozenset(_LOCAL_HASHES)
# The real derived sets, so these tests exercise the same membership rules the
# CLI wires in: read-intent ops whose input model no non-read op shares, plus
# the baseline-matching ops (version-gated at the pre-flight).
_READ_SAFE_OPS = registry_read_safe_ops(REGISTRY)
_LEGACY_SAFE_OPS = registry_legacy_safe_ops(REGISTRY)


def _local_hashes() -> dict[str, str]:
    return dict(_LOCAL_HASHES)


def _meta(op_schemas: dict[str, str] | None, version: str | None = "0.0.0") -> dict[str, object]:
    result: dict[str, object] = {"protocol_version": 1, "supported_ops": []}
    if version is not None:
        result["version"] = version
    if op_schemas is not None:
        result["op_schemas"] = op_schemas
    return {"id": 1, "result": result}


def _invoke(socket: Path, op_id: str, params: dict[str, object]) -> dict[str, object]:
    return cli.invoke_daemon(
        socket, _OPS, _READ_SAFE_OPS, _LEGACY_SAFE_OPS, _local_hashes, op_id, params
    )


def test_invoke_daemon_restarts_idle_daemon_on_schema_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    stopped: list[tuple[Path, Path]] = []
    started: list[tuple[Path, Path]] = []

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            # Stale before the restart, current after it.
            return _meta(_local_hashes() if started else {"new": "stale-new"})
        if method == "status":
            return {"id": 1, "result": {"lanes": 1, "idle": 1, "busy": 0, "active": 0}}
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

    result = _invoke(tmp_path / "dispatchd.sock", "new", {"provider": "claude"})

    assert result == {"ok": True}
    # The op is never forwarded to the stale daemon (its Pydantic models would
    # silently drop fields such as ``provider``): metadata → status → restart →
    # metadata → op.
    assert calls == [CONTROL_META_METHOD, "status", CONTROL_META_METHOD, "new"]
    assert len(stopped) == 1
    assert len(started) == 1


def test_invoke_daemon_blocks_drifted_op_on_busy_stale_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A busy daemon whose ``new`` schema drifted still refuses ``new`` (exit 8)."""
    calls: list[str] = []
    stopped = False

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta({**_local_hashes(), "new": "stale-new"})
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        nonlocal stopped
        stopped = True
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "new", {"provider": "claude"})

    assert exc.value.exit_code == 8
    assert stopped is False
    assert "new" not in calls  # the stale daemon never saw the provider-bearing input
    assert "not restarting automatically" in capsys.readouterr().err


def test_invoke_daemon_allows_hash_matching_ops_on_busy_stale_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Whole-registry drift alone never blocks an op whose own schema matches:
    a busy stale daemon (drifted ``new``) still serves ``roster`` (read) and
    ``stop`` (write), so operators can inspect and drain it."""
    calls: list[str] = []
    stopped = False

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta({**_local_hashes(), "new": "stale-new"})
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        nonlocal stopped
        stopped = True
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)

    assert _invoke(tmp_path / "dispatchd.sock", "roster", {}) == {"ok": True}
    assert _invoke(tmp_path / "dispatchd.sock", "stop", {"lane": "@a"}) == {"ok": True}
    # Matching ops go straight through: no idle probe, no restart attempt.
    assert calls == [CONTROL_META_METHOD, "roster", CONTROL_META_METHOD, "stop"]
    assert stopped is False


def test_invoke_daemon_never_forwards_provider_to_busy_stale_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    stopped = False

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta(None)  # daemon predates the per-op ``op_schemas`` field
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        nonlocal stopped
        stopped = True
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "new", {"provider": "claude"})

    assert exc.value.exit_code == 8
    assert stopped is False
    assert "new" not in calls  # the stale daemon never saw the provider-bearing input
    assert "not restarting automatically" in capsys.readouterr().err


def test_invoke_daemon_treats_missing_metadata_method_as_skew(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        if method == CONTROL_META_METHOD:
            return {"id": 1, "error": {"code": -32601, "message": "unknown op"}}
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    monkeypatch.setattr(cli, "_control_request", request)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "new", {})

    assert exc.value.exit_code == 8
    assert "predates the op-schema handshake" in capsys.readouterr().err


def test_invoke_daemon_allows_baseline_ops_on_parent_version_prehandshake_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pre-handshake daemon that self-reports exactly the parent version
    serves both unshared-input reads (``roster``) and baseline-matching ops
    (``stop``, write-intent) — the baseline proves that release parses them
    identically, so an operator can still cancel runaway work right after
    upgrading, without restarting the daemon mid-flight."""
    calls: list[str] = []

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta(None, version=PARENT_VERSION)
        return {"id": 1, "result": {"ok": True}}

    monkeypatch.setattr(cli, "_control_request", request)

    assert _invoke(tmp_path / "dispatchd.sock", "roster", {}) == {"ok": True}
    assert _invoke(tmp_path / "dispatchd.sock", "stop", {"lane": "@a"}) == {"ok": True}
    assert calls == [CONTROL_META_METHOD, "roster", CONTROL_META_METHOD, "stop"]


def test_invoke_daemon_blocks_baseline_ops_on_older_prehandshake_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The baseline allowance is proven only against the parent release: a
    pre-handshake daemon reporting an OLDER version (e.g. v0.8.2, whose
    ``send`` had no ``content``) may parse baseline ops differently, so
    write-intent baseline ops are blocked (exit 8) while reads still pass —
    v0.10.0 is at the read baseline floor (read schemas proven identical to
    current from that release on)."""
    calls: list[str] = []
    stopped = False

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta(None, version="0.10.0")
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        nonlocal stopped
        stopped = True
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)

    assert _invoke(tmp_path / "dispatchd.sock", "roster", {}) == {"ok": True}

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "stop", {"lane": "@a"})

    assert exc.value.exit_code == 8
    assert stopped is False
    assert "stop" not in calls  # the older daemon never saw the write input
    err = capsys.readouterr().err
    assert "version 0.10.0" in err
    assert "predates the op-schema handshake" in err


def test_invoke_daemon_blocks_all_ops_when_prehandshake_version_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No self-reported version means neither baseline can be trusted: reads
    AND baseline write ops are blocked (exit 8). Deliberate policy change —
    reads used to pass here, but read inputs drift too (v0.8.1's ``roster``
    silently dropped ``parent``), and the version floor cannot be checked
    without a version."""
    calls: list[str] = []

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta(None, version=None)
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    monkeypatch.setattr(cli, "_control_request", request)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "roster", {})
    assert exc.value.exit_code == 8

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "stop", {"lane": "@a"})
    assert exc.value.exit_code == 8

    assert "roster" not in calls
    assert "stop" not in calls
    assert "unreported version" in capsys.readouterr().err


def test_invoke_daemon_blocks_reads_below_read_baseline_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pre-handshake daemon below ``READ_BASELINE_FLOOR`` (e.g. v0.9.0,
    whose ``usage`` output predates the provider runtime summary) is blocked
    even for read-safe ops (exit 8), with the actionable restart hint."""
    calls: list[str] = []

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta(None, version="0.9.0")
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    monkeypatch.setattr(cli, "_control_request", request)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "roster", {})

    assert exc.value.exit_code == 8
    assert "roster" not in calls
    err = capsys.readouterr().err
    assert "version 0.9.0" in err
    assert "dispatch down && dispatch up" in err


def test_invoke_daemon_blocks_all_ops_on_daemon_without_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A daemon without ``__dispatch/metadata`` at all (releases <= 0.8.1)
    reports no version: ALL ops are blocked (exit 8). Deliberate policy
    change — reads used to pass here, but sdist evidence shows 0.8.1's read
    inputs already drifted (``roster`` had no ``parent``/``root``/
    ``ancestor``), so a read would silently return the wrong lanes; the
    restart message is actionable and auto-restart covers the idle case."""
    calls: list[str] = []

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return {"id": 1, "error": {"code": -32601, "message": "unknown op"}}
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    monkeypatch.setattr(cli, "_control_request", request)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "roster", {})
    assert exc.value.exit_code == 8

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "stop", {"lane": "@a"})
    assert exc.value.exit_code == 8

    assert "roster" not in calls
    assert "stop" not in calls
    assert "predates the op-schema handshake" in capsys.readouterr().err


def test_invoke_daemon_blocks_write_shaped_read_op_on_daemon_predating_handshake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``new-plan`` is read-intent but shares ``NewInput`` with ``new``: a
    pre-handshake daemon would silently drop ``provider`` and preview the wrong
    launch, so it gets the same exit-8 refusal as the write op."""
    calls: list[str] = []
    stopped = False

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return {"id": 1, "error": {"code": -32601, "message": "unknown op"}}
        if method == "status":
            return {"id": 1, "result": {"lanes": 2, "idle": 1, "busy": 1, "active": 1}}
        return {"id": 1, "result": {"ok": True}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        nonlocal stopped
        stopped = True
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "new-plan", {"provider": "claude"})

    assert exc.value.exit_code == 8
    assert stopped is False
    assert "new-plan" not in calls  # the legacy daemon never saw the provider-bearing input
    err = capsys.readouterr().err
    assert "predates the op-schema handshake" in err
    assert "dispatch down && dispatch up" in err


def test_invoke_daemon_allows_hash_matching_new_plan_on_hash_capable_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On a daemon that reports op_schemas, ``new-plan`` is gated only by its
    own fingerprint, like every other op."""
    calls: list[str] = []

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta(_local_hashes())
        return {"id": 1, "result": {"ok": True}}

    monkeypatch.setattr(cli, "_control_request", request)

    result = _invoke(tmp_path / "dispatchd.sock", "new-plan", {"provider": "claude"})

    assert result == {"ok": True}
    assert calls == [CONTROL_META_METHOD, "new-plan"]


def test_invoke_daemon_retries_stale_daemon_only_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    starts = 0

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        if method == CONTROL_META_METHOD:
            return _meta({"query": "stale-query"})  # still stale after the restart
        if method == "status":
            return {"id": 1, "result": {"lanes": 0, "idle": 0, "busy": 0, "active": 0}}
        return {"id": 1, "result": {"ok": True}}

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
        _invoke(tmp_path / "dispatchd.sock", "query", {})

    assert exc.value.exit_code == 8
    assert starts == 1
    assert "do not match this CLI" in capsys.readouterr().err


def test_invoke_daemon_reports_restart_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        if method == CONTROL_META_METHOD:
            return _meta({"query": "stale-query"})
        if method == "status":
            return {"id": 1, "result": {"lanes": 0, "idle": 0, "busy": 0, "active": 0}}
        return {"id": 1, "result": {"ok": True}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        return True

    def start(_socket_path: Path, _pidfile: Path) -> bool:
        raise TimeoutError("daemon did not come up")

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.start_detached", start)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "query", {})

    assert exc.value.exit_code == 8
    assert "restart failed" in capsys.readouterr().err


def test_invoke_daemon_falls_back_to_method_not_found_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A daemon replaced between the handshake and the op call still recovers."""
    calls: list[str] = []
    restarted = False

    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        calls.append(method)
        if method == CONTROL_META_METHOD:
            return _meta(_local_hashes())
        if method == "status":
            return {"id": 1, "result": {"lanes": 0, "idle": 0, "busy": 0, "active": 0}}
        if method == "query" and not restarted:
            return {"id": 1, "error": {"code": -32601, "message": "unknown op 'query'"}}
        return {"id": 1, "result": {"ok": True}}

    def stop(_socket_path: Path, _pidfile: Path) -> bool:
        return True

    def start(_socket_path: Path, _pidfile: Path) -> bool:
        nonlocal restarted
        restarted = True
        return True

    monkeypatch.setattr(cli, "_control_request", request)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.stop_daemon", stop)
    monkeypatch.setattr("outfitter.dispatch.daemon.lifecycle.start_detached", start)

    result = _invoke(tmp_path / "dispatchd.sock", "query", {})

    assert result == {"ok": True}
    assert calls == [CONTROL_META_METHOD, "query", "status", CONTROL_META_METHOD, "query"]


def test_invoke_daemon_does_not_treat_unknown_current_cli_typos_as_skew(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def request(_socket: Path, method: str, _params: dict[str, object]) -> dict[str, object]:
        if method == CONTROL_META_METHOD:
            return _meta(_local_hashes())
        return {"id": 1, "error": {"code": -32601, "message": "unknown op 'typo'"}}

    monkeypatch.setattr(cli, "_control_request", request)

    with pytest.raises(typer.Exit) as exc:
        _invoke(tmp_path / "dispatchd.sock", "typo", {})

    assert exc.value.exit_code == 1
