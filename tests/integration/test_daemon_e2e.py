"""End-to-end through the real daemon: CLI-shaped control requests → daemon →
core handlers → real ephemeral app-server. Auto-skips without codex/auth.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


async def _call(
    path: Path, method: str, params: dict[str, object], timeout: float = 60
) -> dict[str, object]:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode())
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout)
    writer.close()
    await writer.wait_closed()
    parsed: dict[str, object] = json.loads(line)
    return parsed


def _result(message: dict[str, object]) -> dict[str, object]:
    result = message["result"]
    assert isinstance(result, dict), message
    return result


async def test_open_send_roster_show_via_real_daemon(
    codex_home: Path,
    work_dir: Path,
    socket_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from outfitter.dispatch.daemon.host import run_daemon

    monkeypatch.setenv("CODEX_HOME", str(codex_home))  # the daemon's app-server uses it
    sock = socket_dir / "dispatchd.sock"
    db = tmp_path / "registry.db"

    daemon = asyncio.create_task(run_daemon(sock, db))
    try:
        for _ in range(200):  # wait for the control socket to come up
            if sock.exists():
                break
            await asyncio.sleep(0.05)
        assert sock.exists(), "daemon control socket did not appear"

        opened = _result(await _call(sock, "open", {"name": "alpha", "cwd": str(work_dir)}))
        lane_id = opened["id"]
        assert opened["handle"] == "@alpha"

        sent = _result(await _call(sock, "send", {"lane": lane_id, "text": "Reply: pong"}))
        assert sent["accepted"] is True  # turn started (we don't wait for completion here)

        roster = _result(await _call(sock, "roster", {}))
        lanes = roster["lanes"]
        assert isinstance(lanes, list)
        assert any(isinstance(lane, dict) and lane["id"] == lane_id for lane in lanes)

        shown = _result(await _call(sock, "show", {"lane": lane_id}))
        assert shown["handle"] == "@alpha"
    finally:
        daemon.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await daemon
