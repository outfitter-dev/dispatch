"""Opt-in lab proving Dispatch dynamic-tool callbacks through real host handlers.

The production package intentionally has no dynamic-tool registration or host
adapter. This spike creates a temporary Python overlay for the custom wheel,
registers allowlisted synthetic tools on ``thread/start``, and forwards
``item/tool/call`` through request/response files to an external lab operator.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from scripts.run_scenario import REPO_ROOT, ScenarioRunner, _load_scenario

OUT = REPO_ROOT / ".agents/notes/pat-176"
DEFAULT_DISPATCH = OUT / "custom-compat/venv/bin/dispatch"
SOURCE_TITLE = "PAT-176 disposable dynamic bridge source"
TARGET_TITLE = "PAT-176 disposable dynamic bridge target"
RENAMED_TITLE = "PAT-176 disposable dynamic bridge target renamed"
RECEIPT = "DYNAMIC_BRIDGE_RECEIPT_001"
TAB_MARKER = "DISPATCH_CUA_MARKER_001"


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one overlay patch anchor in {path}: {old!r}")
    path.write_text(text.replace(old, new))


class DynamicBridgeLab(ScenarioRunner):
    def __init__(self, *, dispatch_bin: str, target_id: str) -> None:
        super().__init__(
            scenario=_load_scenario(REPO_ROOT / "tests/scenarios/basic_coordination.toml"),
            args=argparse.Namespace(dispatch_bin=dispatch_bin, keep_home=False),
        )
        for key in list(self.env):
            if key.startswith("DISPATCH_") and key != "DISPATCH_HOME":
                del self.env[key]
        self.env.pop("PYTHONHOME", None)
        self.env.pop("PYTHONPATH", None)
        self.target_id = target_id
        self.overlay = Path(tempfile.mkdtemp(prefix="dispatch-dynamic-overlay."))
        self.bridge = Path(tempfile.mkdtemp(prefix="dispatch-dynamic-bridge."))
        self.spec_path = self.bridge / "dynamic-tools.json"
        self.env["PYTHONPATH"] = str(self.overlay)
        self.env["DISPATCH_LAB_DYNAMIC_TOOLS_FILE"] = str(self.spec_path)
        self.env["DISPATCH_LAB_TOOL_BRIDGE_DIR"] = str(self.bridge)
        self.env["DISPATCH_INTERACTIVE_REQUEST_TIMEOUT_SECONDS"] = "300"
        self.output = OUT / f"dynamic-bridge-{time.time_ns()}.json"
        self.rows: list[dict[str, Any]] = []
        self.started = time.monotonic()

    def record(self, label: str, value: Any) -> None:
        self.rows.append(
            {
                "label": label,
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                "value": value,
            }
        )
        OUT.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.rows, indent=2) + "\n")
        print(label, flush=True)

    def call(self, label: str, args: list[str], *, timeout: float = 45) -> dict[str, Any]:
        result = self._dispatch_json([*args, "--json"], timeout=timeout)
        self.record(label, result)
        return result

    def prepare_overlay(self) -> None:
        custom_python = Path(self.dispatch_cmd[0]).with_name("python")
        installed = subprocess.run(
            [
                str(custom_python),
                "-c",
                (
                    "import pathlib,outfitter.dispatch as d; "
                    "print(pathlib.Path(d.__file__).parent.parent)"
                ),
            ],
            cwd="/tmp",
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        shutil.copytree(installed, self.overlay / "outfitter")
        package = self.overlay / "outfitter/dispatch"

        models = package / "client/models.py"
        _replace_once(
            models,
            "    model_provider: str | None = None\n"
            '    ephemeral: bool = False\n\n    @model_validator(mode="after")',
            "    model_provider: str | None = None\n"
            "    ephemeral: bool = False\n"
            "    dynamic_tools: list[dict[str, object]] | None = None\n\n"
            '    @model_validator(mode="after")',
        )

        client = package / "client/client.py"
        _replace_once(client, "import asyncio\n", "import asyncio\nimport json\nimport os\n")
        _replace_once(
            client,
            "from types import TracebackType\n",
            "from pathlib import Path\nfrom types import TracebackType\n",
        )
        _replace_once(
            client,
            "        model_provider: str | None = None,\n"
            "        ephemeral: bool = False,\n"
            "    ) -> ThreadInfo:\n"
            "        params = ThreadStartParams(\n",
            "        model_provider: str | None = None,\n"
            "        ephemeral: bool = False,\n"
            "        dynamic_tools: list[dict[str, object]] | None = None,\n"
            "    ) -> ThreadInfo:\n"
            "        if dynamic_tools is None and (lab_spec := os.environ.get(\n"
            '            "DISPATCH_LAB_DYNAMIC_TOOLS_FILE"\n'
            "        )):\n"
            "            loaded = json.loads(Path(lab_spec).read_text())\n"
            "            if not isinstance(loaded, list):\n"
            '                raise ProtocolError("lab dynamic tool spec must be a list")\n'
            "            dynamic_tools = loaded\n"
            "        params = ThreadStartParams(\n",
        )
        _replace_once(
            client,
            "            ephemeral=ephemeral,\n"
            "        )\n"
            '        result = await self._request("thread/start"',
            "            ephemeral=ephemeral,\n"
            "            dynamic_tools=dynamic_tools,\n"
            "        )\n"
            '        result = await self._request("thread/start"',
        )

        manager = package / "core/server_requests.py"
        _replace_once(manager, "import json\n", "import json\nimport os\n")
        _replace_once(
            manager,
            "from datetime import UTC, datetime, timedelta\n",
            "from datetime import UTC, datetime, timedelta\nfrom pathlib import Path\n",
        )
        _replace_once(
            manager,
            '        await _record_request_event(self._ctx, stored, "received")\n'
            "        mode = (\n",
            '        await _record_request_event(self._ctx, stored, "received")\n'
            "        lab_response = await _lab_dynamic_tool_response(stored, request)\n"
            "        if lab_response is not None:\n"
            "            await _send_response(\n"
            "                self._ctx, stored,\n"
            '                PlannedResponse(result=lab_response, summary="lab host handled"),\n'
            "            )\n"
            "            return (\n"
            "                await self._ctx.registry.get_server_request_by_id(_local_id(stored))\n"
            "            ) or stored\n"
            "        mode = (\n",
        )
        helper_anchor = "\n\nasync def respond_to_server_request(\n"
        helper = """

async def _lab_dynamic_tool_response(
    stored: ServerRequest, request: ServerRequestReceived
) -> dict[str, object] | None:
    bridge_value = os.environ.get("DISPATCH_LAB_TOOL_BRIDGE_DIR")
    if request.method != "item/tool/call" or not bridge_value:
        return None
    local_id = _local_id(stored)
    bridge = Path(bridge_value)
    bridge.mkdir(parents=True, exist_ok=True)
    request_path = bridge / f"request-{local_id:03d}.json"
    response_path = bridge / f"response-{local_id:03d}.json"
    payload = {
        "local_request_id": local_id,
        "method": request.method,
        "params": request.raw_params,
    }
    await asyncio.to_thread(request_path.write_text, json.dumps(payload, indent=2) + "\\n")
    deadline = asyncio.get_running_loop().time() + 300
    while asyncio.get_running_loop().time() < deadline:
        if response_path.exists():
            raw = await asyncio.to_thread(response_path.read_text)
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise ValidationError("lab dynamic tool response must be an object")
            return validate_operator_response(request.method, decoded)
        await asyncio.sleep(0.1)
    raise TimeoutError(f"lab dynamic tool response timed out for request {local_id}")
"""
        _replace_once(manager, helper_anchor, helper + helper_anchor)

        self.record(
            "overlay",
            {
                "source_package": installed,
                "overlay": str(self.overlay),
                "patched_files": [
                    "outfitter/dispatch/client/models.py",
                    "outfitter/dispatch/client/client.py",
                    "outfitter/dispatch/core/server_requests.py",
                ],
                "boundary": (
                    "temporary lab-only package overlay; actual custom Dispatch console and "
                    "daemon invocation; no production source or global config changes"
                ),
            },
        )

    def write_specs(self) -> None:
        def object_schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
            return {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }

        target = {"type": "string", "const": self.target_id}
        specs = [
            {
                "type": "namespace",
                "name": "lab_host",
                "description": "Allowlisted real host handlers for one disposable lab target.",
                "tools": [
                    {
                        "type": "function",
                        "name": "read_test_task",
                        "description": "Read only the configured disposable Codex app task.",
                        "inputSchema": object_schema({"thread_id": target}, ["thread_id"]),
                    },
                    {
                        "type": "function",
                        "name": "send_test_message",
                        "description": "Send the exact synthetic marker to the disposable task.",
                        "inputSchema": object_schema(
                            {
                                "thread_id": target,
                                "message": {"type": "string", "const": RECEIPT},
                            },
                            ["thread_id", "message"],
                        ),
                    },
                    {
                        "type": "function",
                        "name": "rename_test_task",
                        "description": "Rename only the disposable task to the exact test title.",
                        "inputSchema": object_schema(
                            {
                                "thread_id": target,
                                "title": {"type": "string", "const": RENAMED_TITLE},
                            },
                            ["thread_id", "title"],
                        ),
                    },
                    {
                        "type": "function",
                        "name": "probe_test_tab",
                        "description": (
                            "Read the exact marker in a new blank test tab, toggle its test "
                            "control twice, and report the restored state."
                        ),
                        "inputSchema": object_schema(
                            {
                                "marker": {"type": "string", "const": TAB_MARKER},
                                "interaction": {"type": "string", "const": "toggle_twice"},
                            },
                            ["marker", "interaction"],
                        ),
                    },
                ],
            }
        ]
        self.spec_path.write_text(json.dumps(specs, indent=2) + "\n")
        self.record("dynamic-tool-specs", specs)

    def prompt(self) -> str:
        return (
            "This is an authorized synthetic lab. Use only the supplied lab_host namespace. "
            f"Read disposable task {self.target_id}; send it exactly {RECEIPT}; rename it "
            f"exactly {RENAMED_TITLE}; read it again to verify the title; then call the blank-tab "
            f"probe with marker {TAB_MARKER} and interaction toggle_twice. Do not use shell, "
            "filesystem, browser substitutes, or any other targets. Finish with a compact result "
            "listing the exact tools called and their returned verification."
        )

    def exercise(self) -> None:
        self._prepare_codex_home()
        self._prepare_work_dir()
        self.prepare_overlay()
        self.write_specs()
        self.record(
            "isolation",
            {
                "dispatch_home": str(self.dispatch_home),
                "codex_home": str(self.codex_home),
                "cwd": str(self.work_dir),
                "bridge": str(self.bridge),
                "copied_from_real_home": ["auth.json"],
                "target_id": self.target_id,
                "model_turn_budget": 1,
            },
        )
        self.record("dispatch-version", self._dispatch(["--version"]).stdout.strip())
        self.call("up", ["up"])
        created = self.call(
            "source-created",
            [
                "new",
                "--name",
                SOURCE_TITLE,
                "--cwd",
                str(self.work_dir),
                "--model",
                "gpt-5.3-codex-spark",
                "--effort",
                "low",
                "--sandbox",
                "read-only",
                "--approval-policy",
                "never",
                "--text",
                self.prompt(),
            ],
        )
        source_id = created["id"]
        seen: set[Path] = set()
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            for request in sorted(self.bridge.glob("request-*.json")):
                if request in seen:
                    continue
                seen.add(request)
                self.record("bridge-request", json.loads(request.read_text()))
                print(f"BRIDGE_REQUEST={request}", flush=True)
            current = self._dispatch_json(["get", source_id, "--json"], timeout=30)
            latest = current.get("latest_turn") or {}
            if latest.get("status") in {"completed", "failed", "interrupted"}:
                self.record("source-completed", current)
                break
            time.sleep(0.5)
        else:
            raise TimeoutError("dynamic bridge source turn did not complete")
        self.call("source-tail", ["tail", source_id, "--limit", "500"])
        self.call(
            "request-ledger",
            ["request", "list", "--lane", source_id, "--state", "responded"],
        )
        requests = [row["value"] for row in self.rows if row["label"] == "bridge-request"]
        tools = [row.get("params", {}).get("tool") for row in requests]
        self.record(
            "verification",
            {
                "observed_tools": tools,
                "required_tools": [
                    "read_test_task",
                    "send_test_message",
                    "rename_test_task",
                    "probe_test_tab",
                ],
                "all_required_observed": all(
                    name in tools
                    for name in (
                        "read_test_task",
                        "send_test_message",
                        "rename_test_task",
                        "probe_test_tab",
                    )
                ),
            },
        )

    def cleanup(self) -> None:
        result = self._dispatch(["down", "--json"], check=False, timeout=30)
        self.record("cleanup-dispatch", {"returncode": result.returncode, "stdout": result.stdout})
        for path in (
            self.dispatch_home,
            self.codex_home,
            self.work_dir,
            self.overlay,
            self.bridge,
        ):
            shutil.rmtree(path, ignore_errors=True)
        self.record("cleanup", "temporary homes, package overlay, bridge, and auth copy removed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-id", required=True, help="Exact disposable Codex app task ID.")
    parser.add_argument("--dispatch-bin", default=str(DEFAULT_DISPATCH))
    args = parser.parse_args()
    lab = DynamicBridgeLab(dispatch_bin=args.dispatch_bin, target_id=args.target_id)
    print(f"evidence={lab.output}", flush=True)
    print(f"bridge={lab.bridge}", flush=True)
    try:
        lab.exercise()
    except BaseException as exc:
        lab.record("lab-error", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        lab.cleanup()


if __name__ == "__main__":
    main()
