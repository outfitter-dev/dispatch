"""Daemon process lifecycle: a detached singleton (ADR-0009) + launchd plist.

``dispatch up`` starts the daemon detached (not a child of the caller, so closing
a shell/MCP client never kills lanes) and singleton (a socket-liveness probe means
no two daemons). ``dispatch down`` stops it. launchd keeps it alive across logins.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def is_daemon_up(socket_path: Path, timeout: float = 0.5) -> bool:
    """Liveness probe: can we connect to the control socket?"""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(socket_path))
        return True
    except OSError:
        return False


def start_detached(socket_path: Path, pidfile: Path, *, wait: float = 15.0) -> bool:
    """Start the daemon detached if not already up. Returns True if it started a
    new daemon, False if one was already running. Raises if it can't come up."""
    if is_daemon_up(socket_path):
        return False  # singleton: another daemon owns the socket
    proc = subprocess.Popen(
        [sys.executable, "-m", "outfitter.dispatch.daemon", "run"],
        start_new_session=True,  # detached: not in the caller's process group
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=dict(os.environ),
    )
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(proc.pid))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if is_daemon_up(socket_path):
            return True
        if proc.poll() is not None:
            raise RuntimeError(f"daemon exited during startup (code {proc.returncode})")
        time.sleep(0.1)
    raise TimeoutError(f"daemon did not come up within {wait}s")


def stop_daemon(socket_path: Path, pidfile: Path) -> bool:
    """Stop the daemon via its pidfile. Returns True if a live daemon was stopped.

    Only signals when a live daemon is actually answering on the socket — so a
    stale pidfile (crashed daemon, recycled PID) can't make us SIGTERM an unrelated
    process. A dead daemon's stale pidfile/socket are cleaned up.
    """
    pid: int | None = None
    if pidfile.exists():
        with contextlib.suppress(ValueError):
            pid = int(pidfile.read_text().strip())

    if not is_daemon_up(socket_path):
        pidfile.unlink(missing_ok=True)  # nothing live here; clear stale state
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()
        return False
    if pid is None:
        return False  # live socket but no usable pid — can't identify the process

    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    pidfile.unlink(missing_ok=True)
    with contextlib.suppress(FileNotFoundError):
        socket_path.unlink()
    return True


def launchd_plist(label: str = "dev.outfitter.dispatchd", program: str | None = None) -> str:
    """Generate a macOS LaunchAgent plist that keeps ``dispatchd`` alive.

    Write to ``~/Library/LaunchAgents/<label>.plist`` and
    ``launchctl load`` it to enable autostart (a deliberate user action).
    """
    dispatchd = program or str(Path(sys.executable).with_name("dispatchd"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"  <key>Label</key><string>{label}</string>\n"
        "  <key>ProgramArguments</key>\n"
        f"  <array><string>{dispatchd}</string><string>run</string></array>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><true/>\n"
        "</dict>\n"
        "</plist>\n"
    )
