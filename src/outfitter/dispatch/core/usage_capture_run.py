"""Daemon-free runtime for ``dispatch usage-capture run``.

This is the high-frequency statusline delegation path: it must never touch the
Dispatch control socket or start the daemon. It reads the full stdin payload
exactly once, captures normalized rate-limit facts through the existing
privacy boundary (which sees at most a bounded prefix and rejects oversize
input), then hands the same bytes — all of them — to the user's original
renderer (per the restoration record) with stdout inherited so output passes
through verbatim. Missing or corrupt records fail visually open: capture what
is safe, emit no stdout, exit 0 so Claude Code shows its built-in footer.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from types import FrameType

from outfitter.dispatch.core.claude_statusline import (
    StatuslineCaptureError,
    capture_claude_statusline,
)
from outfitter.dispatch.core.usage_capture import read_usage_capture_record

MAX_STDIN_BYTES = 1024 * 1024
_FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGINT)
# After forwarding a termination signal, how long the renderer's process group
# gets to exit before it is SIGKILLed. Normal (unsignaled) waits stay unbounded.
_SIGKILL_ESCALATION_SECONDS = 3.0


def run_usage_capture_from_stdin(
    *,
    record_path: Path | None = None,
    snapshot_path: Path | None = None,
) -> int:
    """Read the full stdin payload exactly once, then run the capture path.

    The read is unbounded so the delegated renderer receives every byte Claude
    Code sent; the capture boundary stays bounded inside ``capture_snapshot``.
    """
    payload = sys.stdin.buffer.read()
    return run_usage_capture(payload, record_path=record_path, snapshot_path=snapshot_path)


def capture_snapshot(payload: bytes, *, snapshot_path: Path | None = None) -> None:
    """Capture usage facts from at most a bounded prefix of ``payload``.

    Failures go to stderr (never stdout) and never raise: the statusline hot
    path must not be blocked by a rejected snapshot. An over-cap payload is
    passed through only far enough for the capture boundary to reject it.
    """
    try:
        capture_claude_statusline(payload[: MAX_STDIN_BYTES + 1], path=snapshot_path)
    except (OSError, StatuslineCaptureError):
        print("dispatch: Claude statusline snapshot rejected", file=sys.stderr)


def run_usage_capture(
    payload: bytes,
    *,
    record_path: Path | None = None,
    snapshot_path: Path | None = None,
) -> int:
    """Capture usage facts, then delegate to the original renderer.

    A capture failure never blocks the renderer (diagnostics go to stderr,
    never stdout). When no original renderer existed — or the restoration
    record is missing or corrupt — emit zero stdout and exit 0. The renderer's
    exit code is propagated (128+N when it dies to a signal).
    """
    capture_snapshot(payload, snapshot_path=snapshot_path)

    record = read_usage_capture_record(path=record_path)
    command = record.original_command() if record is not None else None
    if command is None:
        return 0
    return _delegate_to_renderer(command, payload)


def _delegate_to_renderer(command: str, payload: bytes) -> int:
    """Run the original command string through the shell, as Claude Code does.

    stdout/stderr are inherited (verbatim passthrough: multiline, Unicode,
    ANSI colors, OSC 8 links), cwd and environment (including Claude-provided
    COLUMNS/LINES) are inherited, and SIGTERM/SIGINT are forwarded because
    Claude cancels in-flight statusline updates. The renderer runs in its own
    session so signals are forwarded to the whole process group — a non-exec
    ``/bin/sh`` intermediary must not shield the actual renderer.

    A renderer that traps and ignores the forwarded signal must not leave the
    wrapper (and Claude's cancelled statusline invocation) blocked in ``wait()``
    forever: each forwarded signal arms a bounded escalation timer that
    SIGKILLs the process group, and the wrapper then exits with the
    conventional code for the signal it forwarded.

    ``wait()`` returning is not enough after a forwarded signal: for a compound
    or pipeline command the interposed ``/bin/sh`` (the direct child) can die to
    the signal while a signal-ignoring pipeline member survives in the group.
    The escalation therefore stays armed — and the wrapper stays alive — until
    the whole process group is verifiably gone, so a cancelled statusline
    invocation never leaves a renderer lingering behind Claude's back.

    The forwarding handlers are installed BEFORE the child is spawned so no
    delivery window can orphan the renderer: a signal that lands while
    ``process`` is still None is recorded as pending and forwarded immediately
    after ``Popen`` returns, exactly as if it had arrived post-spawn. If the
    spawn itself fails there is no child to orphan and the wrapper simply
    fails visually open (exit 0), matching the no-renderer path.
    """
    process: subprocess.Popen[bytes] | None = None
    pending_signum: int | None = None
    forwarded_signum: int | None = None
    escalation_timers: list[threading.Timer] = []

    def _escalate() -> None:
        # ProcessLookupError (a subclass of OSError) covers the race where the
        # renderer's process group already exited before the signal landed.
        if process is None:  # pragma: no cover — only armed once the child exists
            return
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)

    def _forward_to_group(signum: int) -> None:
        nonlocal forwarded_signum
        if process is None:  # pragma: no cover — callers guarantee the child exists
            return
        forwarded_signum = signum
        with suppress(OSError):
            os.killpg(process.pid, signum)
        # The main thread stays blocked in `process.wait()` (which holds the
        # Popen wait lock), so the bounded escalation runs on a timer thread.
        timer = threading.Timer(_SIGKILL_ESCALATION_SECONDS, _escalate)
        timer.daemon = True
        timer.start()
        escalation_timers.append(timer)

    def _forward(signum: int, _frame: FrameType | None) -> None:
        nonlocal pending_signum
        if process is None:
            # Delivered between arming the handlers and `Popen` returning:
            # record it; the post-spawn pending check forwards it so the
            # renderer can never be orphaned by an early cancellation.
            pending_signum = signum
            return
        _forward_to_group(signum)

    previous = {sig: signal.signal(sig, _forward) for sig in _FORWARDED_SIGNALS}
    try:
        try:
            process = subprocess.Popen(
                command, shell=True, stdin=subprocess.PIPE, start_new_session=True
            )
        except OSError:
            print("dispatch: could not start original statusline renderer", file=sys.stderr)
            return 0
        if pending_signum is not None:
            # A cancellation landed during the spawn window; forward it now
            # that the child (and its process group) exists.
            _forward_to_group(pending_signum)
        stdin = process.stdin
        if stdin is not None:
            try:
                stdin.write(payload)
            except OSError:
                pass  # the renderer may exit without reading stdin
            finally:
                with suppress(OSError):
                    stdin.close()
        returncode = process.wait()
        if forwarded_signum is not None:
            # The shell may have died to the forwarded signal while a
            # signal-ignoring group member lives on; keep the escalation armed
            # until the whole group is gone (bounded by the SIGKILL timer).
            _wait_for_group_exit(process.pid)
    finally:
        for timer in escalation_timers:
            timer.cancel()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if forwarded_signum is not None and returncode == -signal.SIGKILL:
        # The renderer ignored the forwarded signal and died to the escalation;
        # report the signal Claude actually sent, not the SIGKILL cleanup.
        return 128 + forwarded_signum
    return 128 - returncode if returncode < 0 else returncode


def _wait_for_group_exit(pgid: int) -> None:
    """Block until the renderer's process group has no members left.

    Bounded: an armed escalation timer SIGKILLs the group within
    ``_SIGKILL_ESCALATION_SECONDS`` of the forwarded signal. Past that deadline
    (plus margin) the group is SIGKILLed once more directly — a belt-and-braces
    path in case the timer thread was lost — and the loop gives the kill a
    moment to land rather than spinning forever on an unkillable group.
    """
    deadline = time.monotonic() + _SIGKILL_ESCALATION_SECONDS + 1.0
    escalated = False
    while True:
        try:
            os.killpg(pgid, 0)
        except OSError:
            # ProcessLookupError: every group member is gone (the expected
            # exit); other OSErrors mean the group is no longer ours to probe.
            return
        if time.monotonic() >= deadline:
            if escalated:
                return
            escalated = True
            deadline = time.monotonic() + 1.0
            with suppress(OSError):
                os.killpg(pgid, signal.SIGKILL)
        time.sleep(0.02)
