"""Verify an installed local wheel through a synthetic Hermes gateway.

This opt-in smoke installs the supplied wheel into a temporary environment. It
uses no native Hermes profile, credential, service, model, or live Codex socket.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_TIMEOUT_SECONDS = 30.0
_OUTPUT_LIMIT = 4_000
_FIRST_MARKER = "DIS88-SYNTHETIC-FIRST-MARKER"
_SECOND_PROMPT = "Recall the marker from the earlier turn."
_REQUIRED_CAPABILITIES = ["prompt_submit_if_idle_v1", "prompt_turn_correlation_v1"]
_INSTALLED_DOCS = [
    "docs/usage/README.md",
    "docs/usage/deliveries.md",
    "docs/research/hermes-native-provider-contract.md",
    "docs/research/hermes-http-runs-contract.md",
]

_MCP_PROBE = r"""from __future__ import annotations
import anyio
import json
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main() -> None:
    parameters = StdioServerParameters(
        command=sys.argv[1], args=["mcp"], env=dict(os.environ)
    )
    arguments = json.loads(sys.argv[2])
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("dispatch_thread_write", arguments)
    print(json.dumps({
        "is_error": bool(result.isError),
        "structured": result.structuredContent,
        "meta": result.meta,
    }, sort_keys=True))

anyio.run(main)
"""

_ASSET_PROBE = r"""from __future__ import annotations
import json
import outfitter.dispatch as dispatch_package
from importlib.metadata import metadata, version
from importlib.resources import files
from pathlib import Path

root = Path(str(files("outfitter.dispatch").joinpath("assets")))
required = json.loads(__import__("sys").argv[1])
venv = Path(__import__("sys").argv[2]).resolve()
package_file = Path(dispatch_package.__file__).resolve()
if not package_file.is_relative_to(venv):
    raise SystemExit(
        f"outfitter.dispatch resolved outside the isolated environment: {package_file}"
    )
requirements = metadata("outfitter-dispatch").get_all("Requires-Dist") or []
if not any(item.startswith("mcp<2,") or item.startswith("mcp<2;") for item in requirements):
    raise SystemExit(f"installed metadata omitted the MCP <2 bound: {requirements}")
missing = [item for item in required if not (root / item).is_file()]
if missing:
    raise SystemExit("missing installed assets: " + ", ".join(missing))
skill = (root / "skills/dispatch/SKILL.md").read_text()
dm_skill = (root / "skills/dm/SKILL.md").read_text()
plugin = (root / "plugins/dispatch/README.md").read_text()
for needle in (
    "prompt_submit_if_idle_v1",
    "prompt_turn_correlation_v1",
    "stock Hermes",
    "unpublished",
):
    if needle not in skill or needle not in dm_skill or needle not in plugin:
        raise SystemExit(f"installed Hermes guidance omitted {needle!r}")
links = [
    "../../docs/usage/README.md",
    "../../docs/usage/deliveries.md",
    "../../docs/research/hermes-native-provider-contract.md",
    "../../docs/research/hermes-http-runs-contract.md",
]
plugin_root = root / "plugins/dispatch"
for link in links:
    if link not in plugin:
        raise SystemExit(f"plugin README omitted operational link {link}")
    if not (plugin_root / link).resolve().is_file():
        raise SystemExit(f"installed operational link does not resolve: {link}")
print(json.dumps({
    "assets": required,
    "mcp_requirement": next(item for item in requirements if item.startswith("mcp")),
    "mcp_version": version("mcp"),
    "operational_links": links,
    "package_file": str(package_file),
}, sort_keys=True))
"""


class SmokeFailure(RuntimeError):
    """A bounded installed-package assertion failure."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    wheel = args.wheel.expanduser().resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise SystemExit(f"--wheel must name an existing wheel: {wheel}")
    repository = Path(__file__).resolve().parents[1]
    fixture = repository / "tests/fixtures/hermes_package_gateway"
    if not (fixture / "tui_gateway/entry.py").is_file():
        raise SystemExit(f"synthetic gateway fixture is missing: {fixture}")

    root = Path(tempfile.mkdtemp(prefix="dispatch-dis88.", dir="/tmp"))
    evidence: dict[str, Any] = {
        "status": "failed",
        "scope": "installed local wheel with deterministic synthetic Hermes gateway",
        "source": {
            "revision": _source_revision(repository),
            "dirty": _source_dirty(repository),
            "script_sha256": _sha256(Path(__file__)),
            "fixture_sha256": _tree_sha256(fixture),
            "wheel_sha256": _sha256(wheel),
        },
        "required_capabilities": _REQUIRED_CAPABILITIES,
        "evidence_limits": {
            "native_hermes": False,
            "native_model": False,
            "native_persistence": "unknown",
            "transcript_source": "live_observed",
            "transcript_partial": True,
        },
    }
    daemon: subprocess.Popen[str] | None = None
    gateway_pid: int | None = None
    paths: dict[str, Path] | None = None
    success = False
    try:
        paths = _prepare(root, fixture)
        installed = _install(wheel, paths)
        env = _isolated_env(paths)
        schemas = {
            op: _command_json([installed["dispatch"], "schema", op], env=env)
            for op in ("send", "new-plan", "status")
        }
        for op, schema in schemas.items():
            _expect(schema.get("op") == op, f"installed schema mismatch for {op}: {schema}")

        doctor_result = _command(
            [installed["dispatch"], "doctor", "--no-app-server", "--json"],
            env=env,
            accepted_codes={0, 1},
        )
        doctor = _parse_json(doctor_result.stdout, "doctor --no-app-server")
        hermes_check = _named_check(doctor, "hermes_binding")
        _expect(hermes_check.get("status") == "ok", hermes_check)
        _expect(
            hermes_check.get("data", {}).get("configured") is True
            and hermes_check.get("data", {}).get("negotiated") is False,
            hermes_check,
        )
        _expect(not paths["gateway_state"].exists(), "doctor started the Hermes interpreter")

        required_assets = [
            "skills/dispatch/SKILL.md",
            "skills/dm/SKILL.md",
            "plugins/dispatch/README.md",
            "plugins/dispatch/.mcp.json",
            *_INSTALLED_DOCS,
        ]
        assets = _command_json(
            [
                installed["python"],
                "-c",
                _ASSET_PROBE,
                json.dumps(required_assets),
                str(paths["venv"]),
            ],
            env=env,
        )

        daemon_log = (root / "dispatchd.log").open("w")
        daemon = subprocess.Popen(
            [installed["dispatchd"], "run"],
            env=env,
            text=True,
            stdout=daemon_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        daemon_log.close()
        paths["pidfile"].write_text(str(daemon.pid))
        status = _wait_for_hermes_ready(installed["dispatch"], env, daemon)

        first_request = [
            installed["dispatch"],
            "new",
            "--name",
            "synthetic-hermes-package-smoke",
            "--provider",
            "hermes",
            "--cwd",
            str(paths["workspace"]),
            "--text",
            f"Remember {_FIRST_MARKER}.",
            "--idempotency-key",
            "dis88:launch:1",
            "--json",
        ]
        first = _command_json(first_request, env=env)
        first_delivery = _delivery(first)
        first_receipt = _wait_for_delivery(installed["dispatch"], env, str(first_delivery["id"]))
        _expect(first_receipt.get("turn_id") == "synthetic-turn-1", first_receipt)
        lane = str(first["ref"])
        _expect(_submission_count(paths["gateway_state"]) == 1, "first submission count")

        mcp_probe = root / "mcp_probe.py"
        mcp_probe.write_text(_MCP_PROBE)
        second_arguments: dict[str, object] = {
            "op": "send",
            "lane": lane,
            "text": _SECOND_PROMPT,
            "idempotency_key": "dis88:send:2",
        }
        second = _mcp_call(installed, env, mcp_probe, second_arguments)
        _expect(second.get("is_error") is False, second)
        second_structured = second.get("structured")
        if not isinstance(second_structured, dict):
            raise SmokeFailure(f"MCP send omitted structured content: {second}")
        second_delivery = _delivery(second_structured)
        second_receipt = _wait_for_delivery(installed["dispatch"], env, str(second_delivery["id"]))
        _expect(second_receipt.get("turn_id") == "synthetic-turn-2", second_receipt)
        _expect(_submission_count(paths["gateway_state"]) == 2, "second submission count")

        detail = _command_json(
            [installed["dispatch"], "get", lane, "--include-transcript", "--json"], env=env
        )
        transcript = detail.get("transcript")
        if not isinstance(transcript, list):
            raise SmokeFailure(f"thread detail omitted transcript: {detail}")
        texts = [item.get("text") for item in transcript if isinstance(item, dict)]
        _expect(any(isinstance(text, str) and _FIRST_MARKER in text for text in texts), texts)
        sync = detail.get("sync")
        _expect(
            isinstance(sync, dict)
            and sync.get("history_source") == "live_observed"
            and sync.get("transcript_partial") is True,
            sync,
        )

        replay_first = _command_json(first_request, env=env)
        replay_second = _mcp_call(installed, env, mcp_probe, second_arguments)
        _expect(_delivery(replay_first)["id"] == first_delivery["id"], replay_first)
        _expect(
            isinstance(replay_second.get("structured"), dict)
            and _delivery(replay_second["structured"])["id"] == second_delivery["id"],
            replay_second,
        )
        _expect(_submission_count(paths["gateway_state"]) == 2, "replay resubmitted")

        conflict = _mcp_call(
            installed,
            env,
            mcp_probe,
            second_arguments | {"text": "Changed intent must conflict."},
        )
        _expect(conflict.get("is_error") is True, conflict)
        conflict_meta = conflict.get("meta")
        _expect(
            isinstance(conflict_meta, dict)
            and conflict_meta.get("dispatchCode") == "delivery_conflict",
            conflict,
        )
        _expect(_submission_count(paths["gateway_state"]) == 2, "conflict submitted")

        gateway_pid = _gateway_pid(paths["gateway_state"])
        evidence.update(
            {
                "installed": {
                    "version": _command(
                        [installed["dispatch"], "--version"], env=env
                    ).stdout.strip(),
                    "assets": assets["assets"],
                    "mcp_requirement": assets["mcp_requirement"],
                    "mcp_version": assets["mcp_version"],
                    "operational_links": assets["operational_links"],
                },
                "provider": {
                    "state": _provider(status, "hermes")["state"],
                    "supported_actions": _provider(status, "hermes")["supported_actions"],
                    "durability": _provider(status, "hermes")["durability"],
                    "owns_process": _provider(status, "hermes")["owns_process"],
                    "generation_observed": bool(
                        _provider(status, "hermes").get("connection_generation")
                    ),
                },
                "turns": {
                    "submission_count": 2,
                    "first": {
                        "receipt_status": first_receipt["status"],
                        "native_turn_id": first_receipt["turn_id"],
                    },
                    "second": {
                        "receipt_status": second_receipt["status"],
                        "native_turn_id": second_receipt["turn_id"],
                        "assistant_recalled_first_marker": True,
                    },
                },
                "replay": {
                    "launch_same_receipt": True,
                    "send_same_receipt": True,
                    "changed_intent": "delivery_conflict",
                    "additional_submissions": 0,
                },
            }
        )
        success = True
    except (SmokeFailure, OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        evidence["error"] = _bounded(str(exc))
    finally:
        stopped = _stop_processes(daemon, gateway_pid, paths)
        evidence["processes"] = stopped
        success = success and stopped["daemon_stopped"] and stopped["gateway_stopped"]
        evidence["status"] = "passed" if success else "failed"
        evidence_path = root / "evidence.json"
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps(evidence, indent=2, sort_keys=True))
        if success:
            shutil.rmtree(root)
        else:
            print(f"retained_state={root}", file=sys.stderr)
    return 0 if success else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] == "--":
        raw = raw[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path, help="locally built wheel to install")
    return parser.parse_args(raw)


def _prepare(root: Path, fixture: Path) -> dict[str, Path]:
    paths = {
        "venv": root / "venv",
        "dispatch_home": root / "dispatch-home",
        "codex_home": root / "codex-home",
        "hermes_home": root / "hermes-home",
        "fixture": root / "hermes-source",
        "workspace": root / "workspace",
        "codex_socket": root / "absent-codex.sock",
        "gateway_state": root / "hermes-home/package-smoke-state.json",
        "pidfile": root / "dispatch-home/dispatchd.pid",
    }
    for name in ("dispatch_home", "codex_home", "hermes_home", "workspace"):
        paths[name].mkdir(parents=True)
    shutil.copytree(fixture, paths["fixture"])
    _expect(not paths["codex_socket"].exists(), "synthetic Codex socket unexpectedly exists")
    return paths


def _install(wheel: Path, paths: dict[str, Path]) -> dict[str, str]:
    uv = shutil.which("uv")
    if uv is None:
        raise SmokeFailure("uv is required to create the isolated environment")
    _command([uv, "venv", "--python", sys.executable, str(paths["venv"])])
    python = paths["venv"] / "bin/python"
    _command([uv, "pip", "install", "--python", str(python), str(wheel)])
    installed = {
        "python": str(python),
        "dispatch": str(paths["venv"] / "bin/dispatch"),
        "dispatchd": str(paths["venv"] / "bin/dispatchd"),
    }
    for path in installed.values():
        _expect(Path(path).is_file(), f"installed executable missing: {path}")
    return installed


def _isolated_env(paths: dict[str, Path]) -> dict[str, str]:
    env = dict(os.environ)
    for key in tuple(env):
        if (
            key.startswith("DISPATCH_")
            or key.startswith("HERMES_")
            or key in {"CODEX_HOME", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV"}
        ):
            env.pop(key)
    env.update(
        {
            "PATH": str(paths["venv"] / "bin"),
            "DISPATCH_HOME": str(paths["dispatch_home"]),
            "DISPATCH_SOCKET": str(paths["dispatch_home"] / "dispatchd.sock"),
            "DISPATCH_DB": str(paths["dispatch_home"] / "registry.db"),
            "DISPATCH_PIDFILE": str(paths["pidfile"]),
            "DISPATCH_WORKTREE_ROOT": str(paths["dispatch_home"] / "worktrees"),
            "DISPATCH_APP_SERVER_SOCKET": str(paths["codex_socket"]),
            "CODEX_HOME": str(paths["codex_home"]),
            "PYTHONNOUSERSITE": "1",
        }
    )
    config = paths["dispatch_home"] / "config.toml"
    config.write_text(
        "[providers.hermes]\n"
        f'hermes_home = "{paths["hermes_home"]}"\n'
        f'source_root = "{paths["fixture"]}"\n'
        f'interpreter = "{paths["venv"] / "bin/python"}"\n'
        'profile = "default"\n'
    )
    return env


def _command(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    accepted_codes: set[int] | None = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    codes = {0} if accepted_codes is None else accepted_codes
    if result.returncode not in codes:
        raise SmokeFailure(
            f"command {Path(argv[0]).name} {' '.join(argv[1:])} exited {result.returncode}; "
            f"stdout={_bounded(result.stdout)!r}; stderr={_bounded(result.stderr)!r}"
        )
    return result


def _command_json(argv: list[str], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    return _parse_json(_command(argv, env=env).stdout, " ".join(argv[1:]))


def _parse_json(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{label} emitted invalid JSON: {_bounded(value)!r}") from exc
    if not isinstance(parsed, dict):
        raise SmokeFailure(f"{label} emitted non-object JSON")
    return parsed


def _wait_for_hermes_ready(
    dispatch: str, env: dict[str, str], daemon: subprocess.Popen[str]
) -> dict[str, Any]:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    last: object = None
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            raise SmokeFailure(f"dispatchd exited during startup with {daemon.returncode}")
        result = _command(
            [dispatch, "daemon", "status", "--json"], env=env, accepted_codes={0, 1, 8}
        )
        if result.returncode == 0:
            last = _parse_json(result.stdout, "daemon status")
            provider = _provider(last, "hermes")
            if provider.get("state") == "ready":
                _expect(provider.get("supported_actions") == ["launch", "send"], provider)
                _expect(
                    provider.get("durability")
                    == {
                        "local_reservation": True,
                        "provider_idempotency": False,
                        "native_evidence": True,
                    },
                    provider,
                )
                return last
        time.sleep(0.1)
    raise SmokeFailure(f"Hermes provider did not become ready: {last!r}")


def _wait_for_delivery(dispatch: str, env: dict[str, str], receipt_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _command_json([dispatch, "delivery", "get", receipt_id, "--json"], env=env)
        if last.get("status") == "completed":
            return last
        if last.get("status") in {"failed", "ambiguous"}:
            raise SmokeFailure(f"delivery did not complete: {last}")
        time.sleep(0.05)
    raise SmokeFailure(f"delivery did not reach completed: {last}")


def _mcp_call(
    installed: dict[str, str],
    env: dict[str, str],
    probe: Path,
    arguments: dict[str, object],
) -> dict[str, Any]:
    return _command_json(
        [
            installed["python"],
            str(probe),
            installed["dispatch"],
            json.dumps(arguments, separators=(",", ":")),
        ],
        env=env,
    )


def _delivery(value: dict[str, Any]) -> dict[str, Any]:
    delivery = value.get("delivery")
    if not isinstance(delivery, dict) or not isinstance(delivery.get("id"), str):
        raise SmokeFailure(f"result omitted a delivery receipt: {value}")
    return delivery


def _provider(status: dict[str, Any], name: str) -> dict[str, Any]:
    providers = status.get("providers")
    if not isinstance(providers, list):
        raise SmokeFailure(f"status omitted provider diagnostics: {status}")
    for provider in providers:
        if isinstance(provider, dict) and provider.get("provider") == name:
            return provider
    raise SmokeFailure(f"status omitted {name!r} provider diagnostics")


def _named_check(report: dict[str, Any], name: str) -> dict[str, Any]:
    checks = report.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict) and check.get("name") == name:
                return check
    raise SmokeFailure(f"doctor omitted {name!r} check")


def _submission_count(path: Path) -> int:
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or not isinstance(state.get("submissions"), int):
        raise SmokeFailure("synthetic gateway state omitted submission count")
    return int(state["submissions"])


def _gateway_pid(path: Path) -> int:
    state = json.loads(path.read_text())
    pid = state.get("gateway_pid") if isinstance(state, dict) else None
    if not isinstance(pid, int) or pid <= 0:
        raise SmokeFailure("synthetic gateway state omitted gateway pid")
    return pid


def _stop_processes(
    daemon: subprocess.Popen[str] | None,
    gateway_pid: int | None,
    paths: dict[str, Path] | None,
) -> dict[str, bool]:
    if daemon is not None and daemon.poll() is None:
        daemon.send_signal(signal.SIGTERM)
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(daemon.pid, signal.SIGKILL)
            daemon.wait(timeout=5)
    daemon_stopped = daemon is None or daemon.poll() is not None
    if gateway_pid is None and paths is not None and paths["gateway_state"].exists():
        try:
            gateway_pid = _gateway_pid(paths["gateway_state"])
        except (SmokeFailure, OSError, ValueError, json.JSONDecodeError):
            gateway_pid = None
    gateway_stopped = gateway_pid is None or not _pid_alive(gateway_pid)
    if not gateway_stopped and gateway_pid is not None:
        os.kill(gateway_pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pid_alive(gateway_pid):
            time.sleep(0.05)
        gateway_stopped = not _pid_alive(gateway_pid)
    return {"daemon_stopped": daemon_stopped, "gateway_stopped": gateway_stopped}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _source_revision(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SmokeFailure(f"git rev-parse failed: {_bounded(result.stderr)!r}")
    return result.stdout.strip()


def _source_dirty(repository: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _bounded(value: str) -> str:
    return value if len(value) <= _OUTPUT_LIMIT else value[-_OUTPUT_LIMIT:]


def _expect(condition: bool, detail: object) -> None:
    if not condition:
        raise SmokeFailure(f"installed-package assertion failed: {detail!r}")


if __name__ == "__main__":
    sys.exit(main())
