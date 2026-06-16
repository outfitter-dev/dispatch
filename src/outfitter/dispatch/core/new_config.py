"""Configuration/preset resolution for ``dispatch new``.

This is deliberately separate from ``outfitter.dispatch.config``: that module is
runtime storage paths (socket/db/pidfile), while this one is user-authored lane
creation policy from ``.dispatch/config.toml``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
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

_CONFIG_PATH = Path(".dispatch") / "config.toml"
_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_PREFIX_GAP = " "


class NewSettings(BaseModel):
    """Resolved settings for creating a lane and optional initial turn."""

    model_config = ConfigDict(extra="forbid")

    cwd: str | None = None
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
    text: str | None = None
    base_instructions: str | None = None
    base_file: str | None = None
    developer_instructions: str | None = None
    developer_file: str | None = None
    output_schema: dict[str, object] | None = None

    def merged(self, other: NewSettings) -> NewSettings:
        update = other.model_dump(exclude_none=True)
        return self.model_copy(update=update)


class WorkspacePreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "auto"
    worktree: str | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None
    worktree_base: str | None = None


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str | None = None
    worktree: str | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None
    worktree_base: str | None = None
    presets: dict[str, WorkspacePreset] = Field(default_factory=dict)


class NewConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: NewSettings = Field(default_factory=NewSettings)
    presets: dict[str, NewSettings] = Field(default_factory=dict)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    policy: dict[str, object] = Field(default_factory=dict, exclude=True)


class ResolvedNew:
    def __init__(
        self,
        *,
        settings: NewSettings,
        cwd: Path,
        display_name: str,
        handle: str,
        base_instructions: str | None,
        developer_instructions: str | None,
        workspace: WorkspaceConfig,
    ) -> None:
        self.settings = settings
        self.cwd = cwd
        self.display_name = display_name
        self.handle = handle
        self.base_instructions = base_instructions
        self.developer_instructions = developer_instructions
        self.workspace = workspace


def resolve_new(
    *,
    name: str,
    presets: list[str],
    cli: NewSettings,
    packet: NewSettings | None = None,
) -> ResolvedNew:
    """Resolve built-ins, repo config, presets, packet, and CLI overrides for ``new``.

    Precedence (lowest to highest): built-ins < repo ``.dispatch`` defaults <
    presets < ``packet`` (durable launch packet) < CLI flags.
    """

    start_cwd = _absolute(Path(cli.cwd or "."))
    config_path = _find_config(start_cwd)
    config_dir = config_path.parent.parent if config_path is not None else None
    config = _load_config(config_path) if config_path is not None else NewConfigFile()
    if config_dir is not None:
        config = config.model_copy(
            update={"workspace": _resolve_workspace_paths(config.workspace, base=config_dir)}
        )

    settings = NewSettings(
        cwd=str(start_cwd),
        ephemeral=False,
        prefix="[${DISPATCH.CWD.REPO}]",
    ).merged(config.defaults)

    for preset in presets:
        preset_settings = config.presets.get(preset)
        if preset_settings is None:
            raise ValidationError(f"unknown preset {preset!r}")
        settings = settings.merged(preset_settings)

    if packet is not None:
        settings = settings.merged(packet)
    settings = settings.merged(cli)
    cwd = _resolve_path(settings.cwd or str(start_cwd), base=config_dir or start_cwd)
    settings = settings.model_copy(update={"cwd": str(cwd)})
    prefix = _render_prefix(settings.prefix, cwd=cwd, presets=presets)
    display_name = _apply_prefix(name, prefix)

    base_instructions = _resolve_instructions(
        inline=settings.base_instructions, file=settings.base_file, base=config_dir or cwd
    )
    developer_instructions = _resolve_instructions(
        inline=settings.developer_instructions,
        file=settings.developer_file,
        base=config_dir or cwd,
    )
    return ResolvedNew(
        settings=settings,
        cwd=cwd,
        display_name=display_name,
        handle=_handle(display_name),
        base_instructions=base_instructions,
        developer_instructions=developer_instructions,
        workspace=config.workspace,
    )


def _load_config(path: Path) -> NewConfigFile:
    try:
        raw = tomllib.loads(path.read_text())
    except OSError as exc:
        raise ValidationError(f"cannot read dispatch config {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"invalid dispatch config {path}: {exc}") from exc
    try:
        return NewConfigFile.model_validate(_normalize_config(raw))
    except PydanticValidationError as exc:
        raise ValidationError(f"invalid dispatch config {path}: {exc}") from exc


def _normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    if "defaults" in data and isinstance(data["defaults"], dict):
        data["defaults"] = _flatten_instructions(data["defaults"])
    presets = data.get("presets")
    if isinstance(presets, dict):
        data["presets"] = {
            str(name): _flatten_instructions(value) if isinstance(value, dict) else value
            for name, value in presets.items()
        }
    return data


def _flatten_instructions(section: dict[str, Any]) -> dict[str, Any]:
    flattened = dict(section)
    raw_instructions = flattened.pop("instructions", None)
    if isinstance(raw_instructions, dict):
        for key in (
            "base_instructions",
            "base_file",
            "developer_instructions",
            "developer_file",
        ):
            if key in raw_instructions and key not in flattened:
                flattened[key] = raw_instructions[key]
    return flattened


def _resolve_workspace_paths(config: WorkspaceConfig, *, base: Path) -> WorkspaceConfig:
    presets = {
        name: preset.model_copy(
            update={
                "worktree_path": _resolve_optional_path(preset.worktree_path, base=base),
            }
        )
        for name, preset in config.presets.items()
    }
    return config.model_copy(
        update={
            "worktree_path": _resolve_optional_path(config.worktree_path, base=base),
            "presets": presets,
        }
    )


def _find_config(cwd: Path) -> Path | None:
    for path in (cwd, *cwd.parents):
        candidate = path / _CONFIG_PATH
        if candidate.is_file():
            return candidate
    return None


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else (Path.cwd() / expanded).resolve()


def _resolve_path(value: str, *, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _resolve_optional_path(value: str | None, *, base: Path) -> str | None:
    if value is None:
        return None
    return str(_resolve_path(value, base=base))


def _resolve_instructions(*, inline: str | None, file: str | None, base: Path) -> str | None:
    if inline is not None:
        return inline
    if file is None:
        return None
    path = _resolve_path(file, base=base)
    try:
        return path.read_text()
    except OSError as exc:
        raise ValidationError(f"cannot read instructions file {path}: {exc}") from exc


def _render_prefix(template: str | None, *, cwd: Path, presets: list[str]) -> str:
    if template is None:
        return ""
    repo = _repo_name(cwd)
    values = {
        "DISPATCH.CWD.REPO": repo,
        "DISPATCH.CWD.BASENAME": cwd.name,
        "DISPATCH.PRESET": _single_preset(presets),
    }

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = values.get(name)
        if value is None:
            raise ValidationError(f"unsupported prefix variable {name!r}")
        return value

    return _VAR_RE.sub(replace, template).strip()


def _single_preset(presets: list[str]) -> str | None:
    if not presets:
        return None
    if len(presets) == 1:
        return presets[0]
    return presets[-1]


def _repo_name(cwd: Path) -> str:
    for path in (cwd, *cwd.parents):
        if (path / ".git").exists():
            return path.name
    return cwd.name


def _apply_prefix(name: str, prefix: str) -> str:
    bare = name.removeprefix("@").strip()
    if not prefix:
        return bare
    if bare == prefix or bare.startswith(f"{prefix}{_PREFIX_GAP}"):
        return bare
    return f"{prefix}{_PREFIX_GAP}{bare}"


def _handle(display_name: str) -> str:
    return display_name if display_name.startswith("@") else f"@{display_name}"
