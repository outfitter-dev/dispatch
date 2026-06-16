"""Stage launch-packet parts into ``.agents/sessions/<ref>/`` for durability.

Dispatch *writes* staged copies so the worker lane and repo tooling can read the
launch from disk; it never *executes* anything staged (hooks/config are copied,
not run). Staging is additive — protocol-inline fields (goal/prompt/schema/
instructions) are still delivered inline; staged files are durable twins built
from the same resolved bytes.

The writer is synchronous and atomic (build under a temp dir, then ``os.replace``
into place); the async handler calls it via ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from outfitter.dispatch.contracts.errors import StagingError, ValidationError

from .models import StagePart

# Canonical order; file parts map to a filename, dir parts to a packet subdir.
_FILE_PARTS: dict[StagePart, str] = {
    "config": "dispatch.toml",
    "goal": "goal.md",
    "prompt": "prompt.md",
    "output_schema": "output.schema.json",
    "base": "base.md",
    "developer": "developer.md",
}
_DIR_PARTS: dict[StagePart, str] = {"hooks": "hooks", "codex_config": "codex"}
_PART_ORDER: tuple[StagePart, ...] = (
    "config",
    "goal",
    "prompt",
    "output_schema",
    "base",
    "developer",
    "hooks",
    "codex_config",
)
_KNOWN_PARTS: frozenset[StagePart] = frozenset(_PART_ORDER)

_SESSIONS_DIR = Path(".agents") / "sessions"


@dataclass(frozen=True)
class StagePlan:
    parts: tuple[StagePart, ...]


@dataclass(frozen=True)
class StageContent:
    """Resolved content + packet location a staging run needs to write files.

    Content parts (config/goal/prompt/output_schema/base/developer) are the
    already-resolved bytes the launch committed to, so staged files match the
    inlined launch and nothing is re-read from disk mid-stage. Dir parts
    (hooks/codex) are copied from ``packet_path`` since they are never inlined."""

    goal: str | None = None
    prompt: str | None = None
    output_schema: dict[str, object] | None = None
    base: str | None = None
    developer: str | None = None
    config_text: str | None = None
    packet_path: Path | None = None


@dataclass(frozen=True)
class StagedEntry:
    part: StagePart
    path: str
    bytes: int | None
    sha256: str | None


@dataclass(frozen=True)
class StagedResult:
    session_dir: Path
    entries: list[StagedEntry] = field(default_factory=list)


def resolve_stage_plan(
    stage: str | None, inline: str | None, available: frozenset[StagePart]
) -> StagePlan:
    """Resolve ``--stage``/``--inline`` into an ordered set of parts to stage.

    ``stage``: ``None`` stages nothing; ``"all"`` stages every available part; a
    comma list stages exactly those (error if a named part is unavailable).
    ``inline`` removes parts from the staged set (``"all"`` removes everything)."""
    requested = _select(stage, available, flag="--stage")
    dropped = _select(inline, _KNOWN_PARTS, flag="--inline")
    parts = tuple(p for p in _PART_ORDER if p in requested and p not in dropped)
    return StagePlan(parts=parts)


def _select(value: str | None, all_expansion: frozenset[StagePart], *, flag: str) -> set[StagePart]:
    if value is None:
        return set()
    tokens = {token.strip() for token in value.split(",") if token.strip()}
    if not tokens:
        return set()
    if "all" in tokens:
        if tokens != {"all"}:
            raise ValidationError(f"{flag}: 'all' cannot be combined with specific parts")
        return set(all_expansion)
    unknown = tokens - _KNOWN_PARTS
    if unknown:
        valid = ", ".join(_PART_ORDER)
        raise ValidationError(f"{flag}: unknown part(s) {sorted(unknown)}; valid parts: {valid}")
    parts: set[StagePart] = {p for p in _PART_ORDER if p in tokens}
    unavailable = parts - all_expansion
    if unavailable and flag == "--stage":
        raise ValidationError(f"{flag}: cannot stage {sorted(unavailable)}: not available")
    return parts


def stage_session(
    *, cwd: Path, ref: str, lane_id: str, plan: StagePlan, content: StageContent
) -> StagedResult:
    """Atomically write the staged session tree, returning what was written.

    Raises ``StagingError`` on any filesystem failure or if the target session
    directory already exists (no overwrite by default)."""
    sessions = cwd / _SESSIONS_DIR
    target = sessions / ref
    if not _is_within(target, cwd):
        raise StagingError(f"staging target {target} escapes the launch cwd {cwd}")
    if target.exists():
        raise StagingError(f"session directory already exists: {target} (refusing to overwrite)")

    # ``ref`` is registry-assigned and unique per lane, so the temp dir and target
    # never collide across launches; ``os.replace`` into a non-existent target is an
    # atomic same-filesystem rename. OSError below covers any rename/IO failure.
    tmp = sessions / f".staging-{ref}"
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        packet_dir = tmp / "packet"
        packet_dir.mkdir(parents=True)
        (tmp / "scratch").mkdir()
        entries = _write_parts(packet_dir, plan, content)
        _write_state(tmp, lane_id=lane_id, ref=ref, content=content, entries=entries)
        os.replace(tmp, target)
    except StagingError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise StagingError(f"failed to stage session {ref}: {exc}") from exc

    return StagedResult(session_dir=target, entries=entries)


def _write_parts(packet_dir: Path, plan: StagePlan, content: StageContent) -> list[StagedEntry]:
    entries: list[StagedEntry] = []
    for part in plan.parts:
        if part in _FILE_PARTS:
            entries.append(_write_file_part(packet_dir, part, content))
        else:
            entries.append(_copy_dir_part(packet_dir, part, content))
    return entries


def _write_file_part(packet_dir: Path, part: StagePart, content: StageContent) -> StagedEntry:
    text = _file_part_text(part, content)
    if text is None:
        raise StagingError(f"cannot stage {part}: no resolved content")
    name = _FILE_PARTS[part]
    encoded = text.encode("utf-8")
    (packet_dir / name).write_bytes(encoded)
    return StagedEntry(
        part=part,
        path=str(Path("packet") / name),
        bytes=len(encoded),
        sha256=sha256(encoded).hexdigest(),
    )


def _file_part_text(part: StagePart, content: StageContent) -> str | None:
    match part:
        case "config":
            return content.config_text
        case "goal":
            return content.goal
        case "prompt":
            return content.prompt
        case "output_schema":
            if content.output_schema is None:
                return None
            return json.dumps(content.output_schema, indent=2, sort_keys=True)
        case "base":
            return content.base
        case "developer":
            return content.developer
        case _:
            return None


def _copy_dir_part(packet_dir: Path, part: StagePart, content: StageContent) -> StagedEntry:
    name = _DIR_PARTS[part]
    if content.packet_path is None:
        raise StagingError(f"cannot stage {part}: requires a packet")
    src = content.packet_path / name
    if not src.is_dir():
        raise StagingError(f"cannot stage {part}: {src} is not a directory")
    shutil.copytree(src, packet_dir / name)
    return StagedEntry(part=part, path=str(Path("packet") / name), bytes=None, sha256=None)


def _write_state(
    tmp: Path, *, lane_id: str, ref: str, content: StageContent, entries: list[StagedEntry]
) -> None:
    state = {
        "lane_id": lane_id,
        "ref": ref,
        "source_packet": str(content.packet_path) if content.packet_path is not None else None,
        "staged_path": str(tmp.parent / ref),
        "phase": "staged",
        "launch_state": "pending_turn",
        "files": [
            {"part": e.part, "path": e.path, "bytes": e.bytes, "sha256": e.sha256} for e in entries
        ],
    }
    (tmp / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
