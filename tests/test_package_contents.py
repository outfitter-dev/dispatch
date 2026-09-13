"""Verify contributor bootstrap distribution boundaries."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.check_package_contents import _check_sdist, _check_wheel

RUNTIME_SDIST_FILES = {
    "plugins/dispatch/skills/dispatch/SKILL.md",
    "plugins/dispatch/skills/dm/SKILL.md",
    "plugins/dispatch/README.md",
    "plugins/dispatch/.mcp.json",
    "docs/usage/README.md",
    "docs/usage/deliveries.md",
    "docs/research/hermes-native-provider-contract.md",
    "docs/research/hermes-http-runs-contract.md",
    "spikes/claude/assert_probe.py",
    "spikes/claude/sanitize_stream.jq",
    "spikes/claude/zmx_snapshot_probe.sh",
    "spikes/claude/fixtures/capability-policy.json",
    "spikes/claude/fixtures/agent-view-cockpit-plan.jsonl",
    "spikes/claude/fixtures/coexistence-outcomes.jsonl",
    "spikes/claude/fixtures/persistent-owner-completion.jsonl",
    "spikes/claude/fixtures/preflight-nonce-raw.jsonl",
}
CONTRIBUTOR_SDIST_FILES = {"scripts/bootstrap.sh", "tests/test_bootstrap.py"}
RUNTIME_WHEEL_FILES = {
    "outfitter/dispatch/assets/skills/dispatch/SKILL.md",
    "outfitter/dispatch/assets/skills/dm/SKILL.md",
    "outfitter/dispatch/assets/plugins/dispatch/README.md",
    "outfitter/dispatch/assets/plugins/dispatch/.mcp.json",
    "outfitter/dispatch/assets/docs/usage/README.md",
    "outfitter/dispatch/assets/docs/usage/deliveries.md",
    "outfitter/dispatch/assets/docs/research/hermes-native-provider-contract.md",
    "outfitter/dispatch/assets/docs/research/hermes-http-runs-contract.md",
    "outfitter/dispatch/assets/protocol_manifest.json",
}


def _write_sdist(path: Path, names: set[str], *, bootstrap_mode: int = 0o755) -> None:
    root = "outfitter_dispatch-0"
    with tarfile.open(path, "w:gz") as tf:
        for name in names:
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = 0
            info.mode = bootstrap_mode if name == "scripts/bootstrap.sh" else 0o644
            tf.addfile(info)


def _write_wheel(path: Path, names: set[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "")


@pytest.mark.parametrize("missing", sorted(CONTRIBUTOR_SDIST_FILES))
def test_sdist_requires_the_contributor_bootstrap(tmp_path: Path, missing: str) -> None:
    path = tmp_path / "outfitter_dispatch-0.tar.gz"
    _write_sdist(path, RUNTIME_SDIST_FILES | CONTRIBUTOR_SDIST_FILES - {missing})

    with pytest.raises(SystemExit, match=missing):
        _check_sdist(path)


def test_sdist_requires_an_executable_bootstrap(tmp_path: Path) -> None:
    path = tmp_path / "outfitter_dispatch-0.tar.gz"
    _write_sdist(path, RUNTIME_SDIST_FILES | CONTRIBUTOR_SDIST_FILES, bootstrap_mode=0o644)

    with pytest.raises(SystemExit, match="not executable"):
        _check_sdist(path)


def test_sdist_excludes_contributor_agent_adapters(tmp_path: Path) -> None:
    path = tmp_path / "outfitter_dispatch-0.tar.gz"
    _write_sdist(
        path,
        RUNTIME_SDIST_FILES
        | CONTRIBUTOR_SDIST_FILES
        | {".hermes/skills/dispatch-worktree/SKILL.md"},
    )

    with pytest.raises(SystemExit, match="contributor-only"):
        _check_sdist(path)


@pytest.mark.parametrize(
    "contributor_file",
    [
        "scripts/bootstrap.sh",
        "scripts/check_package_contents.py",
        "tests/test_bootstrap.py",
        "tests/test_package_contents.py",
        ".hermes/skills/local/SKILL.md",
    ],
)
def test_wheel_excludes_contributor_only_bootstrap_files(
    tmp_path: Path,
    contributor_file: str,
) -> None:
    path = tmp_path / "outfitter_dispatch-0-py3-none-any.whl"
    _write_wheel(path, RUNTIME_WHEEL_FILES | {contributor_file})

    with pytest.raises(SystemExit, match="contributor-only"):
        _check_wheel(path)
