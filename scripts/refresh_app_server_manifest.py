"""Reduce Codex App Server generated schemas to a reviewable compatibility manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT = Path("tests/fixtures/app_server/protocol_manifest/current.json")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError(f"expected object schema in {path}")
    return value


def _methods(path: Path) -> list[str]:
    schema = _json(path)
    variants = schema.get("oneOf")
    if not isinstance(variants, list):
        raise TypeError(f"expected oneOf request variants in {path}")
    methods: list[str] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        properties = variant.get("properties")
        method = properties.get("method") if isinstance(properties, dict) else None
        values = method.get("enum") if isinstance(method, dict) else None
        if isinstance(values, list):
            methods.extend(value for value in values if isinstance(value, str))
    return sorted(set(methods))


def _properties(path: Path, definition: str | None = None) -> list[str]:
    schema = _json(path)
    if definition is not None:
        definitions = schema.get("definitions")
        schema = definitions.get(definition, {}) if isinstance(definitions, dict) else {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise TypeError(f"expected properties for {definition or path.name} in {path}")
    return sorted(str(name) for name in properties)


def _run(command: list[str], *args: str) -> str:
    completed = subprocess.run(
        [*command, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def build_manifest(command: list[str]) -> dict[str, object]:
    version = _run(command, "--version").removeprefix("codex-cli ")
    with tempfile.TemporaryDirectory(prefix="dispatch-app-server-schema-") as tmp:
        root = Path(tmp)
        stable = root / "stable"
        experimental = root / "experimental"
        _run(command, "app-server", "generate-json-schema", "--out", str(stable))
        _run(
            command,
            "app-server",
            "generate-json-schema",
            "--experimental",
            "--out",
            str(experimental),
        )

        stable_requests = _methods(stable / "ClientRequest.json")
        experimental_requests = _methods(experimental / "ClientRequest.json")
        return {
            "codex_cli_version": version,
            "schema_files": {
                "stable": sum(1 for path in stable.rglob("*") if path.is_file()),
                "experimental": sum(1 for path in experimental.rglob("*") if path.is_file()),
            },
            "client_requests": {
                "stable": stable_requests,
                "experimental_only": sorted(set(experimental_requests) - set(stable_requests)),
            },
            "server_requests": _methods(stable / "ServerRequest.json"),
            "server_notifications": _methods(stable / "ServerNotification.json"),
            "selected_shapes": {
                "model_fields": _properties(
                    stable / "v2" / "ModelListResponse.json", definition="Model"
                ),
                "thread_fields": _properties(
                    stable / "v2" / "ThreadReadResponse.json", definition="Thread"
                ),
                "thread_fork_fields": _properties(stable / "v2" / "ThreadForkParams.json"),
                "thread_list_result_fields": _properties(stable / "v2" / "ThreadListResponse.json"),
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Codex command prefix after -- (default: codex)",
    )
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    manifest = build_manifest(command or ["codex"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
