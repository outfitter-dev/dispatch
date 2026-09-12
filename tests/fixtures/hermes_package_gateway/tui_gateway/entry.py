"""Deterministic JSONL gateway used only by the opt-in installed-wheel smoke."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _state_path() -> Path:
    return Path(os.environ["HERMES_HOME"]) / "package-smoke-state.json"


def _read_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"submissions": 0, "texts": []}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("synthetic gateway state must be an object")
    return value


def _write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
    temporary.replace(path)


def _emit(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(request_id: object, result: dict[str, object]) -> None:
    _emit({"jsonrpc": "2.0", "id": request_id, "result": result})


def _event(event_type: str, session_id: str, turn_id: str, **payload: object) -> None:
    _emit(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": event_type,
                "session_id": session_id,
                "payload": {"turn_id": turn_id, **payload},
            },
        }
    )


def _handle(request: dict[str, Any]) -> None:
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params")
    if not isinstance(params, dict):
        params = {}
    if method == "gateway.capabilities":
        _result(
            request_id,
            {
                "prompt_submit_if_idle_v1": True,
                "prompt_turn_correlation_v1": True,
            },
        )
        return
    if method == "session.create":
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("session.create requires cwd")
        _result(
            request_id,
            {
                "session_id": "synthetic-runtime-1",
                "stored_session_id": "synthetic-stored-1",
                "info": {"cwd": cwd},
            },
        )
        return
    if method == "prompt.submit":
        session_id = params.get("session_id")
        text = params.get("text")
        if session_id != "synthetic-runtime-1" or not isinstance(text, str) or not text:
            raise ValueError("prompt.submit requires the synthetic session and text")
        state = _read_state()
        submissions = int(state.get("submissions", 0)) + 1
        texts = list(state.get("texts", []))
        texts.append(text)
        state = {
            "gateway_pid": state.get("gateway_pid", os.getpid()),
            "submissions": submissions,
            "texts": texts,
        }
        _write_state(state)
        turn_id = f"synthetic-turn-{submissions}"
        assistant_text = (
            f"synthetic accepted {text}" if submissions == 1 else f"synthetic recalled {texts[0]}"
        )
        _event("message.start", session_id, turn_id)
        _event(
            "message.complete",
            session_id,
            turn_id,
            status="complete",
            text=assistant_text,
        )
        _result(request_id, {"status": "streaming", "turn_id": turn_id})
        return
    _emit(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"unsupported method: {method}"},
        }
    )


def main() -> None:
    _write_state({"gateway_pid": os.getpid(), "submissions": 0, "texts": []})
    _emit(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "gateway.ready",
                "payload": {"replay_epoch": "synthetic-package-smoke-v1"},
            },
        }
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            _handle(request)
        except Exception as exc:
            _emit(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(exc)},
                }
            )


if __name__ == "__main__":
    main()
