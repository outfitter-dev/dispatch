"""Opt-in isolated probe for host-provided tools visible to a Dispatch lane.

This uses one real model turn.  It deliberately copies only Codex ``auth.json``
into a temporary home; Desktop config, plugins, and connector credentials are
not copied.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import time
from typing import Any

from scripts.run_scenario import REPO_ROOT, ScenarioRunner, _load_scenario

OUT = REPO_ROOT / ".agents/notes/pat-176"
DEFAULT_DISPATCH = OUT / "custom-compat/venv/bin/dispatch"
TARGET_TITLE = "PAT-176 disposable capability target"
RENAMED_TITLE = "PAT-176 disposable capability target renamed"
RECEIPT = "CAPABILITY_RECEIPT_001"


class CapabilityLab(ScenarioRunner):
    def __init__(self, dispatch_bin: str) -> None:
        super().__init__(
            scenario=_load_scenario(REPO_ROOT / "tests/scenarios/basic_coordination.toml"),
            args=argparse.Namespace(dispatch_bin=dispatch_bin, keep_home=False),
        )
        for key in list(self.env):
            if key.startswith("DISPATCH_") and key != "DISPATCH_HOME":
                del self.env[key]
        self.env.pop("PYTHONHOME", None)
        self.env.pop("PYTHONPATH", None)
        self.socket = self.dispatch_home / "app-server.sock"
        self.env["DISPATCH_APP_SERVER_SOCKET"] = str(self.socket)
        self.output = OUT / f"capabilities-{time.time_ns()}.json"
        self.rows: list[dict[str, Any]] = []
        self.started = time.monotonic()
        self.server: subprocess.Popen[str] | None = None
        self.server_log = self.output.with_suffix(".app-server.log")

    def record(self, label: str, value: Any) -> None:
        self.rows.append(
            {
                "label": label,
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                "value": value,
            }
        )
        OUT.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.rows, indent=2))
        print(label, flush=True)

    def call(self, label: str, args: list[str], *, timeout: float = 45) -> dict[str, Any]:
        result = self._dispatch_json([*args, "--json"], timeout=timeout)
        self.record(label, result)
        return result

    def start_server(self) -> None:
        log = self.server_log.open("w")
        self.server = subprocess.Popen(
            ["codex", "app-server", "--listen", f"unix://{self.socket}"],
            env={**os.environ, "CODEX_HOME": str(self.codex_home)},
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.socket.exists():
                return
            if self.server.poll() is not None:
                raise RuntimeError(
                    f"app-server exited {self.server.returncode}; see {self.server_log}"
                )
            time.sleep(0.1)
        raise TimeoutError("app-server Unix socket did not appear")

    async def rpc_snapshot(self, thread_id: str) -> dict[str, Any]:
        from websockets.asyncio.client import unix_connect

        connection = await unix_connect(
            path=str(self.socket),
            uri="ws://localhost/rpc",
            compression=None,
            max_size=16 * 1024 * 1024,
            proxy=None,
        )
        next_id = 0

        async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal next_id
            next_id += 1
            request_id = next_id
            await connection.send(
                json.dumps({"id": request_id, "method": method, "params": params})
            )
            while True:
                message = json.loads(await asyncio.wait_for(connection.recv(), timeout=30))
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    return {"error": message["error"]}
                return message.get("result", {})

        try:
            initialized = await request(
                "initialize",
                {
                    "clientInfo": {"name": "dispatch-capability-lab", "version": "0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await connection.send(json.dumps({"method": "initialized", "params": {}}))
            installed = await request(
                "app/installed", {"forceRefresh": False, "threadId": thread_id}
            )
            listed = await request(
                "app/list", {"forceRefetch": False, "limit": 10, "threadId": thread_id}
            )
            installed_ids = [
                row["id"]
                for row in installed.get("apps", [])
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            ]
            read = (
                await request(
                    "app/read",
                    {
                        "appIds": installed_ids[:100],
                        "includeTools": True,
                        "threadId": thread_id,
                    },
                )
                if installed_ids
                else {"apps": [], "missingAppIds": []}
            )
            capabilities = await request("modelProvider/capabilities/read", {})
            listed_summary = [
                {key: row.get(key) for key in ("id", "name")}
                for row in listed.get("data", [])
                if isinstance(row, dict)
            ]
            relevant_names = {
                "capture_screen_context",
                "list_threads",
                "read_thread",
                "send_message_to_thread",
                "set_thread_title",
            }
            tool_count = 0
            relevant_tools = []
            for app in read.get("apps", []):
                if not isinstance(app, dict):
                    continue
                for tool in app.get("toolSummaries") or []:
                    if not isinstance(tool, dict):
                        continue
                    tool_count += 1
                    name = tool.get("name")
                    if name in relevant_names or "cua_repl" in str(name).lower():
                        relevant_tools.append(
                            {"app": app.get("name"), "name": name, "title": tool.get("title")}
                        )
            return {
                "initialize": initialized,
                "app_installed": installed,
                "app_list": {
                    "data": listed_summary,
                    "nextCursor_present": listed.get("nextCursor") is not None,
                    "note": "bounded catalog metadata page; not the model tool namespace",
                },
                "app_read_with_tools": {
                    "installed_apps_read": len(read.get("apps", [])),
                    "public_tool_summary_count": tool_count,
                    "relevant_exact_name_matches": relevant_tools,
                    "missingAppIds": read.get("missingAppIds", []),
                    "note": (
                        "connector metadata only; host dynamic tools such as "
                        "mcp__codex_app and mcp__cua_repl are outside this catalog"
                    ),
                },
                "model_provider_capabilities": capabilities,
            }
        finally:
            await connection.close()

    def wait(self, lane: str) -> dict[str, Any]:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = self._dispatch_json(["get", lane, "--json"], timeout=30)
            latest = current.get("latest_turn") or {}
            if latest.get("status") in {"completed", "failed", "interrupted"}:
                self.record("source-completed", current)
                return current
            time.sleep(0.8)
        raise TimeoutError("capability turn did not complete")

    def receipt_snapshot(self, lane: str) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        files = list(self.dispatch_home.glob("*.db")) + list(self.dispatch_home.glob("*.sqlite*"))
        for file in files:
            if file.name.endswith(("-wal", "-shm")):
                continue
            with sqlite3.connect(f"file:{file}?mode=ro", uri=True) as database:
                database.row_factory = sqlite3.Row
                present = {
                    row["name"]
                    for row in database.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                queries = {
                    "deliveries": "SELECT * FROM deliveries WHERE lane = ?",
                    "queued_messages": "SELECT * FROM queued_messages WHERE lane = ?",
                    "message_receipts": "SELECT * FROM message_receipts WHERE lane = ?",
                    "thread_turns": "SELECT * FROM thread_turns WHERE lane = ?",
                }
                for table, query in queries.items():
                    if table not in present:
                        continue
                    rows = database.execute(query, (lane,)).fetchall()
                    snapshot[table] = [dict(row) for row in rows]
        return snapshot

    def exercise(self) -> None:
        self._prepare_codex_home()
        self._prepare_work_dir()
        self.start_server()
        self.record(
            "isolation",
            {
                "dispatch_home": str(self.dispatch_home),
                "codex_home": str(self.codex_home),
                "cwd": str(self.work_dir),
                "copied_from_real_home": ["auth.json"],
                "dispatch_command": self.dispatch_cmd,
                "app_server_transport": str(self.socket),
                "model_turn_budget": 1,
            },
        )
        self.record("dispatch-version", self._dispatch(["--version"]).stdout.strip())
        self.call("doctor", ["doctor"])
        self.call("up", ["up"])
        models = self.call("models", ["models"])
        available = [
            model
            for model in models.get("models", [])
            if "low" in model.get("supported_reasoning_efforts", [])
        ]
        model = next(
            (item for item in available if item["id"] == "gpt-5.3-codex-spark"), available[0]
        )

        target = self.call(
            "target-created",
            [
                "new",
                "--name",
                TARGET_TITLE,
                "--cwd",
                str(self.work_dir),
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
        source = self.call(
            "source-created",
            [
                "new",
                "--name",
                "PAT-176 disposable capability source",
                "--cwd",
                str(self.work_dir),
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
        source_id = source["id"]
        target_id = target["id"]
        self.record("supported-protocol-snapshot", asyncio.run(self.rpc_snapshot(source_id)))

        prompt = (
            "This is an isolated synthetic capability probe. Inspect only the tool definitions "
            "actually supplied to you. If an exact Codex app task tool is available, use it to "
            f"list tasks, read task {target_id}, send that task exactly {RECEIPT}, and rename it "
            f"exactly {RENAMED_TITLE}. If a computer-use JavaScript tool is available, use its "
            "supported API only to open a benign blank data:text/html page or test app and report "
            "the returned result. Do not substitute shell, browser search, filesystem, or claims "
            "for missing tools. Do not call capture_screen_context and do not take a screenshot. "
            "Finish with a compact JSON object naming each exact tool actually called and each "
            "requested capability that was unavailable."
        )
        envelope = json.dumps(
            {
                "source": "capability-lab",
                "event_id": "capability-probe-001",
                "author": "synthetic-lab",
                "body": prompt,
            }
        )
        self.call("source-send-ack", ["send", source_id, envelope])
        self.wait(source_id)
        self.call("source-tail", ["tail", source_id, "--limit", "500"])
        self.call("target-after", ["get", target_id, "--include-transcript"])
        self.call("target-tail", ["tail", target_id, "--limit", "500"])
        self.record("source-durable-receipts", self.receipt_snapshot(source_id))

        snapshots = {row["label"]: row["value"] for row in self.rows}
        source_tail = snapshots["source-tail"]
        target_after = snapshots["target-after"]
        target_tail = snapshots["target-tail"]
        source_completed = snapshots["source-completed"]
        tool_items = [
            item
            for item in source_tail.get("items", [])
            if isinstance(item, dict) and "tool" in str(item.get("type", "")).lower()
        ]
        target_text = json.dumps(target_tail, sort_keys=True)
        target_title = target_after.get("title") or target_after.get("handle")
        self.record(
            "verification",
            {
                "actual_tool_items": tool_items,
                "target_received_exact_marker": RECEIPT in target_text,
                "target_observed_title": target_title,
                "target_was_renamed": str(target_title).endswith(RENAMED_TITLE),
                "installed_thread_start_dynamic_tools_field": False,
                "installed_thread_start_evidence": (
                    "current App Server registers dynamicTools on thread/start; the custom "
                    "wheel ThreadStartParams has no dynamic_tools/dynamicTools field"
                ),
                "health_evidence": {
                    "adapter_reachable": snapshots["up"],
                    "authenticated_provider_catalog": {
                        "model_count": len(models.get("models", [])),
                        "selected_model": model["id"],
                    },
                    "delivery_acceptance": snapshots["source-send-ack"],
                    "durable_receipts": snapshots["source-durable-receipts"],
                    "latest_turn": source_completed.get("latest_turn"),
                    "observed_lane_status": source_completed.get("status"),
                    "boundary": (
                        "These sampled facts separate adapter reachability, provider "
                        "authentication, delivery acceptance/durability, and one turn's "
                        "state. They do not prove inbound wake capability, destination "
                        "readiness, ongoing work, or general online presence."
                    ),
                },
            },
        )

    def cleanup(self) -> None:
        result = self._dispatch(["down", "--json"], check=False, timeout=30)
        self.record("cleanup-dispatch", {"returncode": result.returncode, "stdout": result.stdout})
        if self.server is not None:
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)
        for path in (self.dispatch_home, self.codex_home, self.work_dir):
            shutil.rmtree(path, ignore_errors=True)
        self.record("cleanup", "temporary homes, socket, worktree, and copied auth removed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dispatch-bin",
        default=str(DEFAULT_DISPATCH),
        help="Explicit packaged Dispatch executable or command.",
    )
    args = parser.parse_args()
    lab = CapabilityLab(args.dispatch_bin)
    print(f"evidence={lab.output}", flush=True)
    try:
        lab.exercise()
    except BaseException as exc:
        lab.record("lab-error", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        lab.cleanup()


if __name__ == "__main__":
    main()
