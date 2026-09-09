"""Opt-in PAT-176 lab. Reuse scenario isolation; save synthetic CLI evidence only."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

from scripts.run_scenario import REPO_ROOT, ScenarioRunner, _load_scenario

OUT = REPO_ROOT / ".agents/notes/pat-176"


class Lab(ScenarioRunner):
    def __init__(
        self,
        *,
        remaining_only: bool = False,
        desktop_thread: str | None = None,
        dispatch_bin: str | None = None,
    ) -> None:
        super().__init__(
            scenario=_load_scenario(REPO_ROOT / "tests/scenarios/basic_coordination.toml"),
            args=argparse.Namespace(dispatch_bin=dispatch_bin, keep_home=False),
        )
        # No inherited connection to an existing daemon or provider socket.
        for key in list(self.env):
            if key.startswith("DISPATCH_") and key != "DISPATCH_HOME":
                del self.env[key]
        self.env.pop("PYTHONPATH", None)
        self.env.pop("PYTHONHOME", None)
        self.rows: list[dict[str, object]] = []
        self.output = OUT / f"live-{time.time_ns()}.json"
        self.lane = ""
        self.t0 = time.monotonic()
        self.remaining_only = remaining_only
        self.desktop_thread = desktop_thread

    def record(self, label: str, value: object) -> None:
        self.rows.append(
            {
                "label": label,
                "elapsed_seconds": round(time.monotonic() - self.t0, 3),
                "value": value,
            }
        )
        OUT.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.rows, indent=2))
        print(label, flush=True)

    def call(self, label: str, args: list[str]) -> dict:
        result = self._dispatch_json([*args, "--json"], timeout=45)
        self.record(label, result)
        return result

    def send(self, event: str, prompt: str, mode: str = "send") -> dict:
        envelope = json.dumps(
            {"source": "mock-buzz", "event_id": event, "author": "synthetic-lab", "body": prompt}
        )
        args = ["send", self.lane, envelope, "--mode", mode]
        if mode in {"steer", "context"}:
            result = self._dispatch([*args, "--json"], check=False, timeout=30)
            if result.returncode:
                self.record(
                    event + ":rejected", {"returncode": result.returncode, "stderr": result.stderr}
                )
                return {}
            ack = json.loads(result.stdout)
            self.record(event + ":ack", ack)
            return ack
        return self.call(event + ":ack", args)

    def snapshot(self, label: str) -> dict:
        return self.call(label, ["tail", self.lane, "--limit", "200"])

    def receipts(self, label: str) -> None:
        files = list(self.dispatch_home.glob("*.db")) + list(self.dispatch_home.glob("*.sqlite*"))
        for file in files:
            if file.name.endswith(("-wal", "-shm")):
                continue
            with sqlite3.connect(f"file:{file}?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                for table in ("queued_messages", "message_receipts", "thread_turns"):
                    self.record(
                        label + ":" + table,
                        [dict(row) for row in db.execute(f"SELECT * FROM {table}")],
                    )

    def wait(self, event: str, expected: int) -> None:
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            out = self._dispatch_json(
                ["get", self.lane, "--include-transcript", "--json"], timeout=30
            )
            latest = out.get("latest_turn") or {}
            if latest.get("status") == "failed":
                self.record(event + ":failed", out)
                raise RuntimeError("live provider turn failed; see synthetic evidence")
            if latest.get("status") == "completed":
                tail = self._dispatch_json(["tail", self.lane, "--limit", "200", "--json"])
                turns = {item["turn_id"] for item in tail.get("items", []) if item.get("turn_id")}
                if len(turns) >= expected:
                    self.record(event + ":completed", out)
                    self.record(event + ":tail", tail)
                    self.receipts(event)
                    return
            time.sleep(0.8)
        raise TimeoutError(event)

    def busy(self, event: str, token: str) -> None:
        self.send(
            event,
            f"Use a shell tool to run sleep 12. Wait, then reply exactly {token}. "
            "This is a bounded synthetic delivery test; do no other work.",
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            out = self._dispatch_json(["get", self.lane, "--json"])
            if (out.get("latest_turn") or {}).get("status") == "started":
                self.record(event + ":busy", out)
                return
            time.sleep(0.2)
        raise TimeoutError("busy turn not observed")

    def verify(self) -> None:
        snapshots = {row["label"]: row["value"] for row in self.rows}
        offset = 0 if self.remaining_only else 3
        cases = {
            "steer": (offset + 1, "STEER_001"),
            "context-recall": (offset + 2, "CONTEXT_001"),
            "context-busy": (offset + 3, "CONTEXT_BUSY_001"),
            "duplicate-1": (offset + 4, "DUPLICATE_001"),
            "duplicate-2": (offset + 5, "DUPLICATE_001"),
            "restart": (offset + 6, "RESTART_001"),
        }
        if not self.remaining_only:
            cases = {"idle": (1, "IDLE_001"), "queue": (3, "QUEUE_001"), **cases}
        checks = []
        for name, (count, token) in cases.items():
            items = snapshots[name + ":tail"]["items"]
            turns = {item["turn_id"] for item in items if item.get("turn_id")}
            replies = [item["text"] for item in items if item["type"] == "agentMessage"]
            checks.append(
                {
                    "scenario": name,
                    "expected_turns": count,
                    "observed_turns": len(turns),
                    "expected_reply": token,
                    "observed_reply": replies[-1],
                    "passed": len(turns) == count and replies[-1] == token,
                }
            )
        if not self.remaining_only:
            queue_replies = [
                item["text"]
                for item in snapshots["queue:tail"]["items"]
                if item["type"] == "agentMessage"
            ]
            pending = snapshots["queue-before-completion:queued_messages"]
            sent = snapshots["queue:queued_messages"]
            checks.append(
                {
                    "scenario": "queue-waits-for-busy-reply",
                    "passed": "BUSY_DONE" in queue_replies
                    and queue_replies.index("BUSY_DONE") < queue_replies.index("QUEUE_001")
                    and pending[0]["status"] == "pending"
                    and sent[0]["status"] == "sent",
                }
            )
        context_turns = {item["turn_id"] for item in snapshots["context-no-new-turn"]["items"]}
        checks.append(
            {"scenario": "context-no-new-turn", "passed": len(context_turns) == offset + 1}
        )
        restart_turns = {item["turn_id"] for item in snapshots["restart-history"]["items"]}
        previous_turns = {item["turn_id"] for item in snapshots["duplicate-2:tail"]["items"]}
        checks.append(
            {"scenario": "restart-preserves-history", "passed": restart_turns == previous_turns}
        )
        self.record("verification", checks)
        if not all(check["passed"] for check in checks):
            raise AssertionError("live delivery expectations failed; see verification evidence")

    def exercise(self) -> None:
        self._prepare_codex_home()
        self._prepare_work_dir()
        self.record(
            "isolation",
            {
                "dispatch_home": str(self.dispatch_home),
                "codex_home": str(self.codex_home),
                "cwd": str(self.work_dir),
                "auth": "repo-supported read-only copy; never logged",
                "source": "LIVE Dispatch with mock Buzz envelopes",
                "dispatch_command": self.dispatch_cmd,
            },
        )
        self.record("dispatch-version", self._dispatch(["--version"]).stdout.strip())
        self.call("doctor", ["doctor"])
        self.call("up", ["up"])
        models = self.call("models", ["models"])
        available = [
            m for m in models.get("models", []) if "low" in m.get("supported_reasoning_efforts", [])
        ]
        chosen = next((m for m in available if m["id"] == "gpt-5.3-codex-spark"), available[0])
        lane = self.call(
            "new",
            [
                "new",
                "--name",
                "PAT-176 disposable delivery lab",
                "--cwd",
                str(self.work_dir),
                "--no-send",
                "--no-ephemeral",
                "--model",
                chosen["id"],
                "--effort",
                "low",
                "--sandbox",
                "read-only",
                "--approval-policy",
                "never",
            ],
        )
        self.lane = lane["id"]
        offset = 0
        if not self.remaining_only:
            self.send("evt-idle-001", "Reply exactly IDLE_001. No tools.")
            self.wait("idle", 1)
            self.busy("evt-busy-001", "BUSY_DONE")
            self.send("evt-queue-001", "Reply exactly QUEUE_001. No tools.", "queue")
            self.receipts("queue-before-completion")
            self.wait("queue", 3)
            offset = 3
        with ThreadPoolExecutor(max_workers=1) as pool:
            watch = pool.submit(
                self._dispatch_json,
                ["watch", self.lane, "--limit", "100", "--timeout", "20", "--json"],
            )
            time.sleep(0.5)
            self.busy("evt-steer-base", "ORIGINAL_FINAL")
            time.sleep(2)
            self.send(
                "evt-steer-001",
                "When sleep finishes, change your final answer to STEER_001. Do not cancel sleep.",
                "steer",
            )
            self.record("steer:raw-watch", watch.result())
        self.wait("steer", offset + 1)
        self.send(
            "evt-context-001",
            "Remember the synthetic context code CONTEXT_001 for the next question. "
            "No action requested.",
            "context",
        )
        time.sleep(1)
        self.snapshot("context-no-new-turn")
        self.send(
            "evt-recall-001", "Reply with the synthetic context code just supplied. No tools."
        )
        self.wait("context-recall", offset + 2)
        self.busy("evt-context-busy-base", "CONTEXT_ORIGINAL")
        self.send(
            "evt-context-busy-001",
            "For this synthetic test, change the final reply to CONTEXT_BUSY_001 "
            "after the shell command finishes.",
            "context",
        )
        self.wait("context-busy", offset + 3)
        for count in (4, 5):
            self.send("evt-duplicate-001", "Reply exactly DUPLICATE_001. No tools.")
            self.wait(f"duplicate-{count - 3}", offset + count)
        if self.desktop_thread:
            desktop = self._dispatch(
                ["get", self.desktop_thread, "--json"], check=False, timeout=20
            )
            self.record(
                "desktop-isolated-dispatch-read",
                {
                    "returncode": desktop.returncode,
                    "stdout": desktop.stdout,
                    "stderr": desktop.stderr,
                },
            )
        self.call("restart-down", ["down"])
        unavailable = self._dispatch(["daemon", "status", "--json"], check=False, timeout=10)
        self.record(
            "daemon-unavailable",
            {
                "returncode": unavailable.returncode,
                "stdout": unavailable.stdout,
                "stderr": unavailable.stderr,
            },
        )
        self.call("restart-up", ["up"])
        self.snapshot("restart-history")
        self.send("evt-restart-001", "Reply exactly RESTART_001. No tools.")
        self.wait("restart", offset + 6)
        self.verify()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remaining-only", action="store_true")
    parser.add_argument("--desktop-thread", help="Disposable app task ID; isolated read only.")
    parser.add_argument("--dispatch-bin", help="Explicit packaged Dispatch executable or command.")
    args = parser.parse_args()
    lab = Lab(
        remaining_only=args.remaining_only,
        desktop_thread=args.desktop_thread,
        dispatch_bin=args.dispatch_bin,
    )
    print(f"evidence={lab.output}", flush=True)
    try:
        lab.exercise()
    except BaseException as exc:
        lab.record("lab-error", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        result = lab._dispatch(["down", "--json"], check=False, timeout=30)
        lab.record("cleanup-down", {"returncode": result.returncode, "stdout": result.stdout})
        for path in (lab.dispatch_home, lab.codex_home, lab.work_dir):
            shutil.rmtree(path)
        lab.record("cleanup", "temporary homes and copied auth removed")


if __name__ == "__main__":
    main()
