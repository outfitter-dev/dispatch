"""Codex CLI version compatibility sourced from the protocol manifest."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import total_ordering
from importlib import resources
from pathlib import Path
from typing import Self

_VERSION_PATTERN = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)


@total_ordering
@dataclass(frozen=True)
class CodexVersion:
    """Comparable Codex semver, including multi-part prerelease builds."""

    release: tuple[int, int, int]
    prerelease: tuple[int | str, ...] | None = None

    @classmethod
    def parse(cls, value: str) -> Self:
        match = _VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError(f"unsupported Codex CLI version: {value!r}")
        prerelease = match.group("prerelease")
        identifiers = (
            tuple(int(part) if part.isdigit() else part for part in prerelease.split("."))
            if prerelease is not None
            else None
        )
        return cls(
            release=(
                int(match.group("major")),
                int(match.group("minor")),
                int(match.group("patch")),
            ),
            prerelease=identifiers,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CodexVersion):
            return NotImplemented
        if self.release != other.release:
            return self.release < other.release
        if self.prerelease is None:
            return False
        if other.prerelease is None:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if isinstance(left, int) and isinstance(right, str):
                return True
            if isinstance(left, str) and isinstance(right, int):
                return False
            if isinstance(left, int):
                assert isinstance(right, int)
                return left < right
            assert isinstance(right, str)
            return left < right
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class CodexBinaryCompatibility:
    path: str
    version: str
    minimum_version: str
    supported: bool


def _manifest_path() -> Path:
    packaged = resources.files("outfitter.dispatch").joinpath("assets", "protocol_manifest.json")
    if packaged.is_file():
        return Path(str(packaged))
    return Path(__file__).parents[3] / "tests/fixtures/app_server/protocol_manifest/current.json"


def minimum_codex_cli_version() -> str:
    manifest = json.loads(_manifest_path().read_text())
    value = manifest.get("minimum_codex_cli_version")
    if not isinstance(value, str):
        raise RuntimeError("protocol manifest is missing minimum_codex_cli_version")
    return value


def inspect_codex_binary(binary: str | None = None) -> CodexBinaryCompatibility:
    path = binary or shutil.which("codex")
    if path is None:
        raise FileNotFoundError("codex binary is not visible on PATH")
    completed = subprocess.run(
        [path, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    output = (completed.stdout or completed.stderr).strip()
    version = output.removeprefix("codex-cli ")
    floor = minimum_codex_cli_version()
    return CodexBinaryCompatibility(
        path=path,
        version=version,
        minimum_version=floor,
        supported=CodexVersion.parse(version) >= CodexVersion.parse(floor),
    )
