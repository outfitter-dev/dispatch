"""Cut a GitHub Release for ``pyproject.toml``'s current version when missing.

CI on ``main`` runs this after a green check. Creating the GitHub Release
triggers ``.github/workflows/publish.yml`` (PyPI Trusted Publishing).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@dataclass(frozen=True)
class ReleasePlan:
    version: str
    tag: str
    action: Literal["create", "skip"]
    reason: str


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pyproject = Path(args.pyproject)
    version = read_project_version(pyproject)
    existing = collect_existing_tags()
    plan = plan_release(version=version, existing_tags=existing)
    print(plan.reason)
    if plan.action == "skip" or not args.apply:
        return 0
    create_github_release(plan, target=_release_target(args.target))
    return 0


def read_project_version(pyproject: Path) -> str:
    with pyproject.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    version = project["version"]
    if not isinstance(version, str):
        raise SystemExit(f"{pyproject} project.version is not a string")
    return version


def release_tag_for(version: str) -> str:
    if VERSION_RE.fullmatch(version) is None:
        raise SystemExit(f"refusing to release non-final version {version!r}")
    return f"v{version}"


def plan_release(*, version: str, existing_tags: set[str]) -> ReleasePlan:
    tag = release_tag_for(version)
    normalized = {item.removeprefix("refs/tags/") for item in existing_tags}
    if tag in normalized:
        return ReleasePlan(version, tag, "skip", f"{tag} already exists")
    return ReleasePlan(version, tag, "create", f"create GitHub Release {tag}")


def collect_existing_tags() -> set[str]:
    tags: set[str] = set()
    git = subprocess.run(
        ["git", "tag", "--list", "v*"],
        text=True,
        capture_output=True,
        check=False,
    )
    if git.returncode == 0:
        tags.update(item for item in git.stdout.split() if item)
    listed = subprocess.run(
        ["gh", "release", "list", "--limit", "100", "--json", "tagName"],
        text=True,
        capture_output=True,
        check=False,
    )
    if listed.returncode == 0:
        payload = json.loads(listed.stdout)
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    tag_name = item.get("tagName")
                    if isinstance(tag_name, str) and tag_name:
                        tags.add(tag_name)
    return tags


def create_github_release(plan: ReleasePlan, *, target: str) -> None:
    command = [
        "gh",
        "release",
        "create",
        plan.tag,
        "--title",
        plan.tag,
        "--generate-notes",
        "--target",
        target,
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"gh release create {plan.tag} failed with {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if result.stdout.strip():
        print(result.stdout.strip())


def _release_target(explicit: str | None) -> str:
    if explicit:
        return explicit
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if sha:
        return sha
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit("could not determine release target SHA")
    return result.stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create the GitHub Release; dry-run is the default",
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="path to pyproject.toml (default: ./pyproject.toml)",
    )
    parser.add_argument(
        "--target",
        help="commit SHA to tag; defaults to GITHUB_SHA or HEAD",
    )
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
