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


def _variant_discriminants(path: Path, definition: str, field: str) -> list[str]:
    schema = _json(path)
    definitions = schema.get("definitions")
    shape = definitions.get(definition, {}) if isinstance(definitions, dict) else {}
    variants = shape.get("oneOf") if isinstance(shape, dict) else None
    if not isinstance(variants, list):
        raise TypeError(f"expected oneOf variants for {definition} in {path}")
    values: list[str] = []
    for variant in variants:
        properties = variant.get("properties") if isinstance(variant, dict) else None
        discriminator = properties.get(field) if isinstance(properties, dict) else None
        enum = discriminator.get("enum") if isinstance(discriminator, dict) else None
        if isinstance(enum, list):
            values.extend(value for value in enum if isinstance(value, str))
    return sorted(set(values))


def _enum_values(path: Path, definition: str) -> list[str]:
    schema = _json(path)
    definitions = schema.get("definitions")
    shape = definitions.get(definition, {}) if isinstance(definitions, dict) else {}
    values = shape.get("enum") if isinstance(shape, dict) else None
    if not isinstance(values, list):
        raise TypeError(f"expected enum values for {definition} in {path}")
    return sorted(value for value in values if isinstance(value, str))


def _run(command: list[str], *args: str) -> str:
    completed = subprocess.run(
        [*command, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def build_manifest(command: list[str]) -> dict[str, object]:
    # Record the exact reported version, prerelease suffix included: dispatch
    # pins prerelease builds, and truncating to the release line would claim a
    # stable version that may not exist yet (e.g. 0.148.0-alpha.9 -> "0.148.0").
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
            "thread_item_types": _variant_discriminants(
                stable / "ServerNotification.json", "ThreadItem", "type"
            ),
            "selected_shapes": {
                "model_fields": _properties(
                    stable / "v2" / "ModelListResponse.json", definition="Model"
                ),
                "permission_profile_list_fields": _properties(
                    stable / "v2" / "PermissionProfileListParams.json"
                ),
                "permission_profile_result_fields": _properties(
                    stable / "v2" / "PermissionProfileListResponse.json"
                ),
                "permission_profile_summary_fields": _properties(
                    stable / "v2" / "PermissionProfileListResponse.json",
                    definition="PermissionProfileSummary",
                ),
                "account_result_fields": _properties(stable / "v2" / "GetAccountResponse.json"),
                "account_types": _variant_discriminants(
                    stable / "v2" / "GetAccountResponse.json", "Account", "type"
                ),
                "rate_limit_result_fields": _properties(
                    stable / "v2" / "GetAccountRateLimitsResponse.json"
                ),
                "rate_limit_snapshot_fields": _properties(
                    stable / "v2" / "GetAccountRateLimitsResponse.json",
                    definition="RateLimitSnapshot",
                ),
                "reset_credit_fields": _properties(
                    stable / "v2" / "GetAccountRateLimitsResponse.json",
                    definition="RateLimitResetCredit",
                ),
                "usage_result_fields": _properties(
                    stable / "v2" / "GetAccountTokenUsageResponse.json"
                ),
                "usage_summary_fields": _properties(
                    stable / "v2" / "GetAccountTokenUsageResponse.json",
                    definition="AccountTokenUsageSummary",
                ),
                "thread_fields": _properties(
                    stable / "v2" / "ThreadReadResponse.json", definition="Thread"
                ),
                "thread_start_fields": _properties(stable / "v2" / "ThreadStartParams.json"),
                "thread_start_experimental_fields": _properties(
                    experimental / "v2" / "ThreadStartParams.json"
                ),
                "turn_start_fields": _properties(stable / "v2" / "TurnStartParams.json"),
                "turn_start_user_input_types": _variant_discriminants(
                    stable / "v2" / "TurnStartParams.json", "UserInput", "type"
                ),
                "turn_start_image_details": _enum_values(
                    stable / "v2" / "TurnStartParams.json", "ImageDetail"
                ),
                "turn_start_experimental_fields": _properties(
                    experimental / "v2" / "TurnStartParams.json"
                ),
                "turn_steer_user_input_types": _variant_discriminants(
                    stable / "v2" / "TurnSteerParams.json", "UserInput", "type"
                ),
                "turn_steer_image_details": _enum_values(
                    stable / "v2" / "TurnSteerParams.json", "ImageDetail"
                ),
                "thread_fork_fields": _properties(stable / "v2" / "ThreadForkParams.json"),
                "thread_list_result_fields": _properties(stable / "v2" / "ThreadListResponse.json"),
                "thread_list_experimental_fields": _properties(
                    experimental / "v2" / "ThreadListParams.json"
                ),
                "thread_resume_experimental_fields": _properties(
                    experimental / "v2" / "ThreadResumeParams.json"
                ),
                "thread_resume_result_experimental_fields": _properties(
                    experimental / "v2" / "ThreadResumeResponse.json"
                ),
                "thread_turns_list_fields": _properties(
                    experimental / "v2" / "ThreadTurnsListParams.json"
                ),
                "thread_turns_page_fields": _properties(
                    experimental / "v2" / "ThreadTurnsListResponse.json"
                ),
                "thread_items_list_fields": _properties(
                    experimental / "v2" / "ThreadItemsListParams.json"
                ),
                "thread_items_page_fields": _properties(
                    experimental / "v2" / "ThreadItemsListResponse.json"
                ),
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
