"""Verify built distributions contain the installed-user support assets."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path


def main() -> None:
    dist = Path("dist")
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit("expected exactly one wheel and one sdist in dist/")
    _check_wheel(wheels[0])
    _check_sdist(sdists[0])


def _check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    required = {
        "outfitter/dispatch/assets/protocol_manifest.json",
        "outfitter/dispatch/assets/skills/dispatch/SKILL.md",
        "outfitter/dispatch/assets/skills/dm/SKILL.md",
        "outfitter/dispatch/assets/plugins/dispatch/README.md",
        "outfitter/dispatch/assets/plugins/dispatch/.mcp.json",
        "outfitter/dispatch/assets/protocol_manifest.json",
    }
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"{path.name} missing required files: {', '.join(missing)}")


def _check_sdist(path: Path) -> None:
    with tarfile.open(path) as tf:
        names = {member.name.partition("/")[2] for member in tf.getmembers()}
    required = {
        "plugins/dispatch/skills/dispatch/SKILL.md",
        "plugins/dispatch/skills/dm/SKILL.md",
        "plugins/dispatch/README.md",
        "plugins/dispatch/.mcp.json",
        "spikes/claude/assert_probe.py",
        "spikes/claude/sanitize_stream.jq",
        "spikes/claude/zmx_snapshot_probe.sh",
        "spikes/claude/fixtures/capability-policy.json",
        "spikes/claude/fixtures/agent-view-cockpit-plan.jsonl",
        "spikes/claude/fixtures/coexistence-outcomes.jsonl",
        "spikes/claude/fixtures/persistent-owner-completion.jsonl",
        "spikes/claude/fixtures/preflight-nonce-raw.jsonl",
    }
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"{path.name} missing required files: {', '.join(missing)}")


if __name__ == "__main__":
    main()
