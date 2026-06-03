"""Real detached-daemon lifecycle: up (singleton) → status → down.

Spawns a real detached ``dispatchd`` (which owns its own ephemeral app-server
under a temp CODEX_HOME). Auto-skips without codex/auth.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _call(socket_path: Path, method: str, params: dict[str, object]) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect(str(socket_path))
        sock.sendall((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode())
        buffer = bytearray()
        while b"\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer.extend(chunk)
    parsed: dict[str, object] = json.loads(buffer)
    return parsed


def test_up_status_down_detached_singleton(
    codex_home: Path, socket_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from outfitter.dispatch.daemon import lifecycle

    sock = socket_dir / "dispatchd.sock"
    pidfile = tmp_path / "dispatchd.pid"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("DISPATCH_SOCKET", str(sock))
    monkeypatch.setenv("DISPATCH_DB", str(tmp_path / "registry.db"))
    monkeypatch.setenv("DISPATCH_PIDFILE", str(pidfile))

    started = False
    try:
        started = lifecycle.start_detached(sock, pidfile, wait=30)
        assert started is True
        assert lifecycle.is_daemon_up(sock)

        # Singleton: a second start is a no-op (does not spawn a second daemon).
        assert lifecycle.start_detached(sock, pidfile) is False

        # status routes to the detached daemon.
        response = _call(sock, "status", {})
        result = response.get("result")
        assert isinstance(result, dict)
        assert result["lanes"] == 0
    finally:
        if started:
            assert lifecycle.stop_daemon(sock, pidfile) is True
            for _ in range(50):
                if not lifecycle.is_daemon_up(sock):
                    break
                time.sleep(0.1)
            assert not lifecycle.is_daemon_up(sock)
