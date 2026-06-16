"""Launch packets: durable directories describing one lane launch.

A packet is a plain directory of files. Dispatch *reads* a blessed subset to
build a native launch (goal/prompt/output-schema/instructions/settings) and
records everything else without inferring behavior. Hook/config staging and
execution policy live elsewhere; this module never runs anything.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from outfitter.dispatch.client.models import (
    ApprovalPolicy,
    ApprovalsReviewer,
    Effort,
    Personality,
    ReasoningSummary,
    ThreadSandbox,
)
from outfitter.dispatch.contracts.errors import ValidationError

from .new_config import NewSettings

_GOAL_FILE = "goal.md"
_PROMPT_FILE = "prompt.md"
_BASE_FILE = "base.md"
_DEVELOPER_FILE = "developer.md"
_OUTPUT_SCHEMA_FILE = "output.schema.json"
_CONFIG_FILE = "dispatch.toml"
_AUX_DIRS = ("hooks", "codex")

_KNOWN_ENTRIES = frozenset(
    {
        _GOAL_FILE,
        _PROMPT_FILE,
        _BASE_FILE,
        _DEVELOPER_FILE,
        _OUTPUT_SCHEMA_FILE,
        _CONFIG_FILE,
        *_AUX_DIRS,
    }
)


class _PacketConfig(BaseModel):
    """The safe ``dispatch.toml`` subset: lane settings, not content or location.

    Content (goal/prompt/schema/instructions) comes from the packet's own files;
    ``cwd`` stays a launch-time argument so packets remain portable.
    """

    model_config = ConfigDict(extra="forbid")

    sandbox: ThreadSandbox | None = None
    approval_policy: ApprovalPolicy | None = None
    approvals_reviewer: ApprovalsReviewer | None = None
    model: str | None = None
    model_provider: str | None = None
    effort: Effort | None = None
    summary: ReasoningSummary | None = None
    personality: Personality | None = None
    service_tier: str | None = None
    ephemeral: bool | None = None
    prefix: str | None = None

    def to_settings(self) -> NewSettings:
        return NewSettings(**self.model_dump(exclude_none=True))


@dataclass(frozen=True)
class PacketContent:
    path: Path
    settings: NewSettings
    goal: str | None = None
    prompt: str | None = None
    base: str | None = None
    developer: str | None = None
    output_schema: dict[str, object] | None = None
    has_config: bool = False
    unknown_files: list[str] = field(default_factory=list)
    aux_dirs: list[str] = field(default_factory=list)


def load_packet(path: Path) -> PacketContent:
    """Read a packet directory into structured content. Raises ``ValidationError``
    for a missing directory or malformed known files; unknown entries are recorded,
    never fatal."""
    if not path.is_dir():
        raise ValidationError(f"packet {str(path)!r} is not a directory")

    settings = _load_config(path / _CONFIG_FILE)
    output_schema = _load_output_schema(path / _OUTPUT_SCHEMA_FILE)

    unknown_files: list[str] = []
    aux_dirs: list[str] = []
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.name in _KNOWN_ENTRIES:
            if entry.name in _AUX_DIRS and entry.is_dir():
                aux_dirs.append(entry.name)
            continue
        unknown_files.append(entry.name)

    return PacketContent(
        path=path,
        settings=settings,
        goal=_read_text(path / _GOAL_FILE),
        prompt=_read_text(path / _PROMPT_FILE),
        base=_read_text(path / _BASE_FILE),
        developer=_read_text(path / _DEVELOPER_FILE),
        output_schema=output_schema,
        has_config=(path / _CONFIG_FILE).is_file(),
        unknown_files=unknown_files,
        aux_dirs=aux_dirs,
    )


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"cannot read packet file {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValidationError(f"packet file {path} is not valid UTF-8: {exc}") from exc


def _load_config(path: Path) -> NewSettings:
    if not path.is_file():
        return NewSettings()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"cannot read packet {_CONFIG_FILE}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"invalid packet {_CONFIG_FILE} {path}: {exc}") from exc
    try:
        return _PacketConfig.model_validate(raw).to_settings()
    except PydanticValidationError as exc:
        raise ValidationError(f"invalid packet {_CONFIG_FILE} {path}: {exc}") from exc


def _load_output_schema(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"cannot read packet {_OUTPUT_SCHEMA_FILE}: {exc}") from exc
    return parse_output_schema(raw, where=f"packet {_OUTPUT_SCHEMA_FILE} {path}")


def parse_output_schema(raw: str, *, where: str) -> dict[str, object]:
    """Parse a JSON Schema document; require a JSON object (schema root)."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid {where}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"invalid {where}: expected a JSON object")
    return value
