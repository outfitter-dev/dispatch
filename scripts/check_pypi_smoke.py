"""Smoke-test the published PyPI package from a clean Dispatch home.

This is intentionally not part of ``just check``: it installs from PyPI with
``uvx`` and starts a real daemon/app-server. Run it after publishing or when
validating the clean-install path tracked by GitHub issue #27.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    package_spec = args.package_spec or f"outfitter-dispatch=={_project_version()}"
    home = Path(tempfile.mkdtemp(prefix="dispatch-pypi-smoke."))
    env = os.environ.copy()
    env["DISPATCH_HOME"] = str(home)
    print(f"DISPATCH_HOME={home}")
    print(f"package={package_spec}")
    try:
        version = _dispatch(package_spec, ["--version"], env)
        _expect(version.stdout.strip().startswith("dispatch "), version.stdout)

        schema = _dispatch_json(package_spec, ["schema", "models"], env)
        _expect(schema.get("op") == "models", "schema op is not models")
        _expect(_path(schema, "input", "properties", "refresh", "type") == "boolean", schema)
        _expect(_path(schema, "output", "properties", "models", "type") == "array", schema)

        up = _dispatch_json(package_spec, ["up", "--json"], env, timeout=args.timeout)
        _expect(up.get("status") in {"started", "running"}, up)

        models = _dispatch_json(package_spec, ["models", "--json"], env, timeout=args.timeout)
        _expect(models.get("source") == "app-server", models)
        _expect(_nonempty_list(models.get("models")), models)
        configured = models.get("configured_default")
        _expect(isinstance(configured, dict), models)
        _expect(isinstance(configured.get("model"), str), models)

        cached = _dispatch_json(
            package_spec, ["models", "--no-refresh", "--json"], env, timeout=args.timeout
        )
        _expect(cached.get("source") == "registry", cached)
        _expect(_nonempty_list(cached.get("models")), cached)

        lanes = _dispatch_json(package_spec, ["list", "--json"], env)
        _expect(isinstance(lanes.get("lanes"), list), lanes)

        down = _dispatch_json(package_spec, ["down", "--json"], env)
        _expect(down.get("status") == "stopped", down)
        print("PyPI clean-install smoke passed")
        return 0
    finally:
        if not args.keep_home:
            _dispatch(package_spec, ["down", "--json"], env, check=False)
            shutil.rmtree(home, ignore_errors=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    raw_args = sys.argv[1:] if argv is None else argv
    if raw_args and raw_args[0] == "--":
        raw_args = raw_args[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-spec",
        help="uvx package spec to install, e.g. outfitter-dispatch==0.5.0",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="seconds to allow each daemon/app-server command",
    )
    parser.add_argument(
        "--keep-home",
        action="store_true",
        help="keep the temporary DISPATCH_HOME for debugging",
    )
    return parser.parse_args(raw_args)


def _project_version() -> str:
    with Path("pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    version = project["version"]
    if not isinstance(version, str):
        raise SystemExit("pyproject.toml project.version is not a string")
    return version


def _dispatch(
    package_spec: str,
    args: list[str],
    env: dict[str, str],
    *,
    timeout: float = 90.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["uvx", "--from", package_spec, "dispatch", *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise SystemExit(
            f"dispatch {' '.join(args)} failed with {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _dispatch_json(
    package_spec: str,
    args: list[str],
    env: dict[str, str],
    *,
    timeout: float = 90.0,
) -> dict[str, Any]:
    result = _dispatch(package_spec, args, env, timeout=timeout)
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"dispatch {' '.join(args)} did not produce JSON\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"dispatch {' '.join(args)} produced non-object JSON")
    return parsed


def _path(data: dict[str, Any], *parts: str) -> Any:
    value: Any = data
    for part in parts:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _nonempty_list(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0


def _expect(condition: bool, detail: object) -> None:
    if not condition:
        raise SystemExit(f"PyPI smoke assertion failed: {detail!r}")


if __name__ == "__main__":
    sys.exit(main())
