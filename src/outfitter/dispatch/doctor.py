"""Local install and runtime diagnostics for ``dispatch doctor``."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from outfitter.dispatch import config
from outfitter.dispatch.client.client import AppServerClient
from outfitter.dispatch.client.models import ClientInfo
from outfitter.dispatch.client.transport import StdioTransport
from outfitter.dispatch.daemon.lifecycle import is_daemon_up
from outfitter.dispatch.registry.store import SCHEMA_VERSION
from outfitter.dispatch.version import package_version

DoctorStatus = Literal["ok", "warn", "fail"]


class DoctorCheck(BaseModel):
    name: str
    status: DoctorStatus
    summary: str
    detail: str | None = None
    recovery: str | None = None
    data: dict[str, object] = Field(default_factory=dict)


class DoctorReport(BaseModel):
    status: DoctorStatus
    version: str
    checks: list[DoctorCheck]


@dataclass(frozen=True)
class DoctorOptions:
    app_server: bool = True
    timeout: float = 10.0


def run_doctor(options: DoctorOptions | None = None) -> DoctorReport:
    opts = options or DoctorOptions()
    checks = [
        _path_check(),
        _codex_binary_check(),
        _codex_auth_check(),
        _daemon_state_check(),
        _registry_check(),
        _asset_check(),
    ]
    if opts.app_server:
        checks.append(asyncio.run(_app_server_check(opts.timeout)))
    else:
        checks.append(
            DoctorCheck(
                name="app_server",
                status="warn",
                summary="app-server smoke skipped",
                recovery="Re-run without --no-app-server before relying on live lane operations.",
            )
        )
    return DoctorReport(status=_overall(checks), version=package_version(), checks=checks)


def render_text(report: DoctorReport) -> str:
    lines = [f"dispatch doctor {report.status} (dispatch {report.version})"]
    for check in report.checks:
        lines.append(f"[{check.status}] {check.name}: {check.summary}")
        if check.detail:
            lines.append(f"  detail: {check.detail}")
        if check.recovery:
            lines.append(f"  next: {check.recovery}")
    return "\n".join(lines)


def _overall(checks: list[DoctorCheck]) -> DoctorStatus:
    if any(check.status == "fail" for check in checks):
        return "fail"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"


def _path_check() -> DoctorCheck:
    dispatch = shutil.which("dispatch")
    dispatchd = shutil.which("dispatchd")
    data: dict[str, object] = {
        "python": sys.executable,
        "dispatch": dispatch,
        "dispatchd": dispatchd,
        "path": os.environ.get("PATH", ""),
    }
    if dispatch and dispatchd:
        return DoctorCheck(
            name="path",
            status="ok",
            summary="dispatch and dispatchd are visible on PATH",
            data=data,
        )
    return DoctorCheck(
        name="path",
        status="warn",
        summary="one or more console scripts are not visible on PATH",
        recovery=(
            "Install with `uv tool install outfitter-dispatch` or run from source with "
            "`uv run dispatch ...`; if already installed, run `uv tool update-shell`."
        ),
        data=data,
    )


def _codex_binary_check() -> DoctorCheck:
    codex = shutil.which("codex")
    if codex is None:
        return DoctorCheck(
            name="codex_binary",
            status="fail",
            summary="codex binary is not visible on PATH",
            recovery="Install or expose Codex CLI before starting dispatchd.",
        )
    try:
        proc = subprocess.run(
            [codex, "--version"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorCheck(
            name="codex_binary",
            status="fail",
            summary="codex binary could not report a version",
            detail=str(exc),
            recovery="Verify `codex --version` works in the same shell or Codex context.",
            data={"path": codex},
        )
    output = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        return DoctorCheck(
            name="codex_binary",
            status="fail",
            summary="codex --version failed",
            detail=output,
            recovery="Fix the Codex CLI install before starting dispatchd.",
            data={"path": codex, "returncode": proc.returncode},
        )
    return DoctorCheck(
        name="codex_binary",
        status="ok",
        summary=output or "codex version command succeeded",
        data={"path": codex, "version": output},
    )


def _codex_auth_check() -> DoctorCheck:
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    candidates = [codex_home / "auth.json", codex_home / "credentials.json"]
    present = [path.name for path in candidates if path.exists()]
    if present:
        return DoctorCheck(
            name="codex_auth",
            status="ok",
            summary="Codex auth material is present",
            detail="Auth file contents were not read.",
            data={"codex_home": str(codex_home), "files": present},
        )
    return DoctorCheck(
        name="codex_auth",
        status="warn",
        summary="no Codex auth file was found",
        recovery="Run `codex login` or start Codex once, then re-run `dispatch doctor`.",
        data={"codex_home": str(codex_home)},
    )


def _daemon_state_check() -> DoctorCheck:
    socket_path = config.socket_path()
    pidfile = config.pidfile_path()
    db_path = config.db_path()
    live = is_daemon_up(socket_path)
    pid = _read_pid(pidfile)
    data: dict[str, object] = {
        "socket": str(socket_path),
        "socket_exists": socket_path.exists(),
        "pidfile": str(pidfile),
        "pid": pid,
        "db": str(db_path),
        "daemon_reachable": live,
    }
    if live:
        status = _query_daemon_status(socket_path)
        data["status"] = status
        return DoctorCheck(
            name="daemon",
            status="ok",
            summary="dispatchd is reachable",
            data=data,
        )
    if socket_path.exists() or pidfile.exists():
        return DoctorCheck(
            name="daemon",
            status="warn",
            summary="dispatchd is not reachable and stale runtime files may exist",
            recovery=(
                "Run `dispatch down` to clear stale pid/socket state, then `dispatch up`; "
                f"runtime paths are under {socket_path.parent}."
            ),
            data=data,
        )
    return DoctorCheck(
        name="daemon",
        status="warn",
        summary="dispatchd is not running",
        recovery="Run `dispatch up` when you are ready to manage lanes.",
        data=data,
    )


def _read_pid(pidfile: Path) -> int | None:
    if not pidfile.exists():
        return None
    with contextlib.suppress(ValueError, OSError):
        return int(pidfile.read_text().strip())
    return None


def _query_daemon_status(socket_path: Path) -> dict[str, object]:
    request = json.dumps({"id": 1, "method": "status", "params": {}}) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(str(socket_path))
            sock.sendall(request.encode())
            line = sock.recv(16384)
    except OSError as exc:
        return {"error": str(exc)}
    with contextlib.suppress(json.JSONDecodeError):
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            result = parsed.get("result")
            if isinstance(result, dict):
                return result
            error = parsed.get("error")
            if isinstance(error, dict):
                return {"error": error}
    return {"error": "daemon returned an unusable status response"}


def _registry_check() -> DoctorCheck:
    path = config.db_path()
    data: dict[str, object] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return DoctorCheck(
            name="registry",
            status="warn",
            summary="registry database does not exist yet",
            recovery="Run `dispatch up`; the daemon creates the registry on first start.",
            data=data,
        )
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            row_counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("lanes", "triggers")
                if table in tables
            }
    except sqlite3.Error as exc:
        return DoctorCheck(
            name="registry",
            status="fail",
            summary="registry database could not be opened",
            detail=str(exc),
            recovery="Move the damaged registry aside or set DISPATCH_DB to a fresh path.",
            data=data,
        )
    expected = {
        "lanes",
        "triggers",
        "actions_log",
        "queued_messages",
        "lane_sync_sources",
        "lane_snapshots",
        "model_catalog",
        "lane_model_settings",
        "lane_runtime_settings",
    }
    data.update(
        {
            "schema_version": version,
            "supported_schema_version": SCHEMA_VERSION,
            "integrity": integrity,
            "tables": sorted(tables),
            "row_counts": row_counts,
        }
    )
    if integrity != "ok":
        return DoctorCheck(
            name="registry",
            status="fail",
            summary="registry quick_check failed",
            detail=str(integrity),
            recovery="Back up the registry and recreate it, or inspect with sqlite3.",
            data=data,
        )
    if version > SCHEMA_VERSION:
        return DoctorCheck(
            name="registry",
            status="fail",
            summary="registry schema is newer than this dispatch binary supports",
            recovery="Upgrade outfitter-dispatch before starting the daemon.",
            data=data,
        )
    missing = sorted(expected - tables)
    if version == 0:
        detail = "missing tables: " + ", ".join(missing) if missing else None
        return DoctorCheck(
            name="registry",
            status="warn",
            summary="registry schema is unversioned",
            detail=detail,
            recovery=(
                "Back up the registry, then run `dispatch down`, `dispatch registry migrate`, "
                "and `dispatch up` to apply compatibility migrations."
            ),
            data=data,
        )
    if version < SCHEMA_VERSION:
        return DoctorCheck(
            name="registry",
            status="warn",
            summary="registry schema is older than this dispatch binary supports",
            detail=f"{version} < {SCHEMA_VERSION}",
            recovery=(
                "Run `dispatch down`, `dispatch registry migrate`, then `dispatch up` "
                "to apply compatibility migrations."
            ),
            data=data,
        )
    if missing:
        return DoctorCheck(
            name="registry",
            status="fail",
            summary="registry is missing required tables",
            detail=", ".join(missing),
            recovery=(
                "Run `dispatch down`, `dispatch registry migrate`, then `dispatch up`. "
                "If migration fails, inspect the backup path from `registry migrate`."
            ),
            data=data,
        )
    return DoctorCheck(name="registry", status="ok", summary="registry is readable", data=data)


def _asset_check() -> DoctorCheck:
    candidates = _asset_candidates()
    data: dict[str, object] = {
        "candidates": [
            {"skills": str(skills), "plugin": str(plugin)} for skills, plugin in candidates
        ]
    }
    missing: list[str] = []
    for skills, plugin in candidates:
        missing = []
        if not (skills / "dispatch" / "SKILL.md").is_file():
            missing.append("dispatch skill")
        if not (skills / "dm" / "SKILL.md").is_file():
            missing.append("dm skill")
        if not (plugin / ".mcp.json").is_file():
            missing.append("plugin MCP config")
        if not missing:
            return DoctorCheck(
                name="agent_assets",
                status="ok",
                summary="dispatch skills and plugin assets are present",
                data={"skills": str(skills), "plugin": str(plugin)},
            )
    if missing:
        return DoctorCheck(
            name="agent_assets",
            status="warn",
            summary="packaged skills/plugin assets are incomplete",
            detail=", ".join(missing),
            recovery="Use the repo checkout's skills/ and plugins/dispatch/ assets.",
            data=data,
        )
    raise AssertionError("asset candidate list must not be empty")


def _asset_candidates() -> list[tuple[Path, Path]]:
    package_root = resources.files("outfitter.dispatch")
    repo_root = Path(__file__).resolve().parents[3]
    return [
        (
            Path(str(package_root / "assets" / "skills")),
            Path(str(package_root / "assets" / "plugins" / "dispatch")),
        ),
        (repo_root / "skills", repo_root / "plugins" / "dispatch"),
    ]


async def _app_server_check(timeout: float) -> DoctorCheck:
    codex = shutil.which("codex")
    if codex is None:
        return DoctorCheck(
            name="app_server",
            status="fail",
            summary="app-server smoke could not run because codex is missing",
            recovery="Fix the codex_binary check first.",
        )
    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="dispatch-doctor-") as tmp:
        env.setdefault("TMPDIR", tmp)
        transport = StdioTransport(env=env)
        client = AppServerClient(transport)
        try:
            await asyncio.wait_for(transport.start(), timeout=timeout)
            await client.start()
            result = await asyncio.wait_for(
                client.initialize(ClientInfo(name="dispatch-doctor", version=package_version())),
                timeout=timeout,
            )
        except Exception as exc:
            detail = str(exc) or exc.__class__.__name__
            stderr_tail = (
                transport.stderr_tail() if hasattr(transport, "stderr_tail") else ""
            ).strip()
            if stderr_tail:
                detail = f"{detail}; stderr: {stderr_tail[-1000:]}"
            return DoctorCheck(
                name="app_server",
                status="fail",
                summary="codex app-server initialize smoke failed",
                detail=detail,
                recovery=(
                    "Verify `codex app-server --listen stdio://` starts in this shell; "
                    "then re-run `dispatch doctor`."
                ),
            )
        finally:
            await client.close()
        return DoctorCheck(
            name="app_server",
            status="ok",
            summary="codex app-server initialized successfully",
            data={
                "user_agent": result.user_agent,
                "codex_home": result.codex_home,
                "platform": result.platform_family or result.platform_os,
            },
        )
