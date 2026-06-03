"""Daemon process lifecycle: liveness probe, singleton, launchd plist."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from outfitter.dispatch.daemon.lifecycle import (
    is_daemon_up,
    launchd_plist,
    start_detached,
    stop_daemon,
)


def test_is_daemon_up_false_for_missing_socket(socket_dir: Path) -> None:
    assert is_daemon_up(socket_dir / "nope.sock") is False


def test_stop_daemon_returns_false_without_pidfile(socket_dir: Path, tmp_path: Path) -> None:
    assert stop_daemon(socket_dir / "s.sock", tmp_path / "nope.pid") is False


def test_launchd_plist_has_keepalive_and_runatload() -> None:
    plist = launchd_plist(label="dev.test.dispatchd")
    assert "<key>Label</key><string>dev.test.dispatchd</string>" in plist
    assert "<key>KeepAlive</key><true/>" in plist
    assert "<key>RunAtLoad</key><true/>" in plist
    assert "<string>run</string>" in plist


@pytest_asyncio.fixture
async def live_socket(socket_dir: Path) -> AsyncIterator[Path]:
    path = socket_dir / "dispatchd.sock"

    async def _noop(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_unix_server(_noop, path=str(path))
    try:
        yield path
    finally:
        server.close()
        await server.wait_closed()


async def test_is_daemon_up_true_for_served_socket(live_socket: Path) -> None:
    assert is_daemon_up(live_socket) is True


async def test_start_detached_is_singleton_noop_when_up(live_socket: Path, tmp_path: Path) -> None:
    # A daemon already owns the socket → start_detached must not spawn a second one.
    assert start_detached(live_socket, tmp_path / "d.pid") is False
