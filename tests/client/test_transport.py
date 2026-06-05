"""Unit tests for the stdio App Server transport."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

import pytest

from outfitter.dispatch.client.transport import StdioTransport


async def test_stdio_transport_sets_reader_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class DummyProcess:
        returncode = None
        stdin = None
        stdout = None
        stderr = None

    async def fake_create_subprocess_exec(*argv: str, **kwargs: Any) -> DummyProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    transport = StdioTransport(argv=("codex", "app-server"), read_limit=123_456)
    await transport.start()

    assert captured["argv"] == ("codex", "app-server")
    assert captured["kwargs"]["limit"] == 123_456
    assert captured["kwargs"]["stdin"] is asyncio.subprocess.PIPE
    assert captured["kwargs"]["stdout"] is asyncio.subprocess.PIPE
    assert captured["kwargs"]["stderr"] is asyncio.subprocess.PIPE


def test_stdio_transport_accepts_sequence_argv() -> None:
    argv: Sequence[str] = ("codex", "app-server", "--listen", "stdio://")
    transport = StdioTransport(argv=argv)
    assert transport.returncode is None


async def test_stdio_transport_reads_large_jsonl_line() -> None:
    payload = {"id": 1, "result": {"text": "x" * 70_000}}
    script = (
        "import json, sys; "
        f"sys.stdout.write({json.dumps(json.dumps(payload))} + '\\n'); "
        "sys.stdout.flush()"
    )
    transport = StdioTransport(
        argv=(sys.executable, "-c", script),
        read_limit=128 * 1024,
    )
    await transport.start()
    try:
        assert await transport.receive() == payload
    finally:
        await transport.close()
