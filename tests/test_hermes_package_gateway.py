"""Protocol coverage for the opt-in installed-package Hermes fixture."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def test_package_gateway_emits_correlated_turns_and_counts_submissions(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures/hermes_package_gateway"
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    env["PYTHONPATH"] = str(source)
    process = subprocess.Popen(
        [sys.executable, "-m", "tui_gateway.entry"],
        cwd=source,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        ready = _read(process)
        assert ready["params"]["type"] == "gateway.ready"

        _write(process, 1, "gateway.capabilities", {})
        capabilities = _read(process)
        assert capabilities["result"] == {
            "prompt_submit_if_idle_v1": True,
            "prompt_turn_correlation_v1": True,
        }

        _write(
            process,
            2,
            "session.create",
            {"profile": "default", "cwd": str(tmp_path), "title": "Synthetic"},
        )
        created = _read(process)
        assert created["result"] == {
            "session_id": "synthetic-runtime-1",
            "stored_session_id": "synthetic-stored-1",
            "info": {"cwd": str(tmp_path)},
        }

        first_text = "remember DIS88-SYNTHETIC-FIRST-MARKER"
        _write(
            process,
            3,
            "prompt.submit",
            {"session_id": "synthetic-runtime-1", "text": first_text, "if_idle": True},
        )
        first = [_read(process) for _ in range(3)]
        assert first[0]["params"]["payload"]["turn_id"] == "synthetic-turn-1"
        assert first[1]["params"]["payload"]["status"] == "complete"
        assert first[2]["result"] == {"status": "streaming", "turn_id": "synthetic-turn-1"}

        _write(
            process,
            4,
            "prompt.submit",
            {
                "session_id": "synthetic-runtime-1",
                "text": "recall the earlier marker",
                "if_idle": True,
            },
        )
        second = [_read(process) for _ in range(3)]
        assert second[0]["params"]["payload"]["turn_id"] == "synthetic-turn-2"
        assert first_text in second[1]["params"]["payload"]["text"]
        assert second[2]["result"] == {"status": "streaming", "turn_id": "synthetic-turn-2"}

        state = json.loads((hermes_home / "package-smoke-state.json").read_text())
        assert state["gateway_pid"] == process.pid
        assert state["submissions"] == 2
        assert state["texts"] == [first_text, "recall the earlier marker"]
    finally:
        process.stdin.close()
        process.wait(timeout=5)
    assert process.returncode == 0


def _write(
    process: subprocess.Popen[str], request_id: int, method: str, params: dict[str, object]
) -> None:
    assert process.stdin is not None
    process.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n"
    )
    process.stdin.flush()


def _read(process: subprocess.Popen[str]) -> dict[str, Any]:
    assert process.stdout is not None
    value = json.loads(process.stdout.readline())
    assert isinstance(value, dict)
    return value
