"""Opt-in installed-package proof: real provider turns, no fault injection."""

from __future__ import annotations

import argparse
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

from .live_delivery import OUT, Lab


def exercise(lab: Lab) -> None:
    lab._prepare_codex_home()
    lab._prepare_work_dir()
    lab.record(
        "isolation",
        {
            "dispatch_home": str(lab.dispatch_home),
            "codex_home": str(lab.codex_home),
            "cwd": str(lab.work_dir),
            "executable": lab.dispatch_cmd,
        },
    )
    lab.call("up", ["up"])
    models = lab.call("models", ["models"])["models"]
    available = [m for m in models if "low" in m.get("supported_reasoning_efforts", [])]
    model = next((m for m in available if m["id"] == "gpt-5.3-codex-spark"), available[0])
    lane = lab.call(
        "new",
        [
            "new",
            "--name",
            "DIS-72 private package test",
            "--cwd",
            str(lab.work_dir),
            "--no-send",
            "--no-ephemeral",
            "--model",
            model["id"],
            "--effort",
            "low",
            "--sandbox",
            "read-only",
            "--approval-policy",
            "never",
        ],
    )
    lab.lane = lane["id"]
    args = [
        "send",
        lab.lane,
        "Reply exactly KEYED_001. No tools.",
        "--idempotency-key",
        "lab-event-1",
    ]
    first = lab.call("first", args)["delivery"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        replays = list(pool.map(lambda _: lab._dispatch_json([*args, "--json"]), range(4)))
    lab.record("concurrent-replays", replays)
    assert all(r["delivery"]["id"] == first["id"] for r in replays)
    lab.wait("first", 1)
    receipt = lab.call("first-receipt", ["delivery", "get", first["id"]])
    assert receipt["status"] == "completed" and receipt["turn_id"]
    conflict_args = [*args]
    conflict_args[2] = "Different payload must not be submitted."
    conflict = lab._dispatch([*conflict_args, "--json"], check=False)
    lab.record(
        "conflict",
        {"returncode": conflict.returncode, "stdout": conflict.stdout, "stderr": conflict.stderr},
    )
    assert conflict.returncode == 2
    lab.call("down", ["down"])
    lab.call("restart", ["up"])
    restarted = lab.call("replay-after-restart", args)["delivery"]
    assert restarted["id"] == first["id"] and restarted["status"] == "completed"
    assert (
        len({i["turn_id"] for i in lab.snapshot("restart-history")["items"] if i.get("turn_id")})
        == 1
    )
    busy = lab.call(
        "busy",
        [
            "send",
            lab.lane,
            "Use the shell tool to run sleep 8, then reply exactly BUSY_002. Do no other work.",
            "--idempotency-key",
            "lab-event-2",
        ],
    )["delivery"]
    queued = lab.call(
        "queued",
        [
            "send",
            lab.lane,
            "Reply exactly QUEUE_003. No tools.",
            "--mode",
            "queue",
            "--idempotency-key",
            "lab-event-3",
        ],
    )["delivery"]
    assert queued["status"] == "queued"
    lab.wait("queue", 3)
    for label, delivery in (("busy", busy), ("queue", queued)):
        final = lab.call(label + "-receipt", ["delivery", "get", delivery["id"]])
        assert final["status"] == "completed" and final["turn_id"]
    history = lab.call(
        "provider-history", ["history", lab.lane, "--view", "items", "--raw", "--limit", "100"]
    )
    inputs = [i["raw"] for i in history["items"] if i.get("raw", {}).get("type") == "userMessage"]
    assert len(inputs) == 3
    assert {i["clientId"] for i in inputs} == {first["id"], busy["id"], queued["id"]}
    lab.record(
        "verification",
        {
            "passed": True,
            "provider_turns": 3,
            "native_receipt_ids": [i["clientId"] for i in inputs],
            "cases": [
                "same-key concurrency",
                "conflicting reuse",
                "receipt lookup",
                "clean restart replay",
                "busy queue",
                "completion",
                "persisted native IDs",
            ],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispatch-bin", required=True)
    lab = Lab(dispatch_bin=parser.parse_args().dispatch_bin)
    lab.output = OUT / f"reliability-{time.time_ns()}.json"
    print(f"evidence={lab.output}", flush=True)
    try:
        exercise(lab)
    except BaseException as exc:
        lab.record("error", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        down = lab._dispatch(["down", "--json"], check=False, timeout=30)
        lab.record("cleanup-down", {"returncode": down.returncode})
        for path in (lab.dispatch_home, lab.codex_home, lab.work_dir):
            shutil.rmtree(path)
        lab.record("cleanup", "temporary homes/auth removed; isolated daemon stopped")


if __name__ == "__main__":
    main()
