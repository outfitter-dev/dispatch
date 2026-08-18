"""Integration harness: a REAL ephemeral ``codex app-server`` over an isolated
``CODEX_HOME``.

Never touches the user's live ``~/.codex`` as CODEX_HOME: a temp dir is used and
``auth.json`` is copied in read-only so the ephemeral server can reach the model.
Lanes are ``ephemeral:true`` (or archived). Auto-skips when the ``codex`` binary
or auth is unavailable (e.g. CI), so these never block the unit gate.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.transport import StdioTransport
from outfitter.dispatch.codex_compat import inspect_codex_binary

_REAL_AUTH = Path.home() / ".codex" / "auth.json"


def _why_unavailable() -> str | None:
    codex = shutil.which("codex")
    if codex is None:
        return "codex binary not on PATH"
    try:
        compatibility = inspect_codex_binary(codex)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return f"codex version unavailable: {exc}"
    if not compatibility.supported:
        return (
            f"codex-cli {compatibility.version} at {compatibility.path} is below supported "
            f"floor {compatibility.minimum_version}"
        )
    if not _REAL_AUTH.exists():
        return "no ~/.codex/auth.json (model auth) available"
    return None


@pytest.fixture
def codex_home() -> Iterator[Path]:
    reason = _why_unavailable()
    if reason is not None:
        pytest.skip(f"integration unavailable: {reason}")
    tmp = Path(tempfile.mkdtemp(prefix="dispatch-it-codex-"))
    shutil.copy2(_REAL_AUTH, tmp / "auth.json")  # read-only copy; user's ~/.codex untouched
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def work_dir() -> Iterator[Path]:
    tmp = Path(tempfile.mkdtemp(prefix="dispatch-it-work-"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def connect(codex_home: Path) -> AppServerClient:
    """Spawn an ephemeral app-server bound to ``codex_home`` and initialize it."""
    transport = StdioTransport(env={"CODEX_HOME": str(codex_home), "PATH": _path()})
    await transport.start()
    client = AppServerClient(transport)
    await client.start()
    await client.initialize()
    return client


def _path() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")


@pytest_asyncio.fixture
async def client(codex_home: Path) -> AsyncIterator[AppServerClient]:
    c = await connect(codex_home)
    try:
        yield c
    finally:
        await c.close()


@pytest_asyncio.fixture
async def elicitation_client(codex_home: Path) -> AsyncIterator[AppServerClient]:
    server = Path(__file__).parents[1] / "fixtures" / "mcp" / "elicitation_server.py"
    (codex_home / "config.toml").write_text(
        "[mcp_servers.dispatch_elicitation_probe]\n"
        f"command = {json.dumps(sys.executable)}\n"
        f"args = [{json.dumps(str(server))}]\n"
    )
    c = await connect(codex_home)
    try:
        yield c
    finally:
        await c.close()
