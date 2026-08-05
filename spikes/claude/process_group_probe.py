#!/usr/bin/env python3
"""Prove exact POSIX process-group ownership with a disposable fake tree."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).with_name("fake_process_tree.sh")


def running(pid: int) -> bool:
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    state = result.stdout.strip()
    return result.returncode == 0 and bool(state) and not state.startswith("Z")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dispatch-claude-pgid-") as temp:
        process_file = Path(temp) / "processes"
        env = {**os.environ, "DISPATCH_CLAUDE_PROCESS_FILE": str(process_file)}
        process = subprocess.Popen(
            [str(SCRIPT)],
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if process_file.exists():
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError("fake process tree did not start")

            parent, child = map(int, process_file.read_text().split())
            if parent != process.pid or os.getpgid(parent) != parent:
                raise RuntimeError("parent identity/pgid mismatch")
            if os.getpgid(child) != parent:
                raise RuntimeError("child escaped the owned process group")

            os.killpg(parent, signal.SIGINT)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(parent, signal.SIGTERM)
                process.wait(timeout=2)

            if running(parent) or running(child):
                raise RuntimeError("owned process group did not exit")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    main()
