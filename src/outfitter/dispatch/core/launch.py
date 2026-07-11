"""Resolve a ``dispatch new`` launch from packet + file + inline inputs.

This is a pure function (filesystem reads only, no daemon/app-server/registry
side effects), shared by the launching handler (``new``) and the mutation-free
preview handler (``new-plan``). Precedence per launch slot is::

    inline (CLI flag / stdin) > explicit file > packet > repo config

Each slot is owned wholesale by exactly one layer, so a CLI ``--base-file`` is
never shadowed by a packet's ``base.md`` and vice versa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from outfitter.dispatch.contracts.errors import ValidationError

from .models import LaunchOrigin, LaunchSlot, NewInput, StagePart
from .new_config import NewSettings, ResolvedNew, resolve_new
from .packet import PacketContent, load_packet, parse_output_schema
from .staging import StagePlan, resolve_stage_plan

# Which layer owns a launch slot's value (drives precedence + reported origin).
_Owner = Literal["cli", "packet", "config"]


@dataclass(frozen=True)
class LaunchSource:
    slot: LaunchSlot
    origin: LaunchOrigin
    path: str | None
    bytes: int
    sha256: str


@dataclass(frozen=True)
class ResolvedLaunch:
    resolved: ResolvedNew
    goal: str | None
    text: str | None
    output_schema: dict[str, object] | None
    packet: PacketContent | None
    sources: list[LaunchSource]
    unknown_files: list[str]
    aux_dirs: list[str]
    would_send: bool
    stage_plan: StagePlan


def resolve_launch(inp: NewInput) -> ResolvedLaunch:
    base = _absolute(Path(inp.cwd or "."))
    packet = load_packet(_resolve_path(inp.packet, base)) if inp.packet else None

    goal, goal_src = _resolve_goal(inp, packet, base)
    text, text_owner = _resolve_text(inp, packet, base)
    schema, schema_owner = _resolve_output_schema(inp, packet, base)
    base_owner = _instruction_owner(inp.base_instructions, inp.base_file, packet, "base")
    dev_owner = _instruction_owner(
        inp.developer_instructions, inp.developer_file, packet, "developer"
    )

    packet_layer = _packet_layer(packet, text_owner, schema_owner, base_owner, dev_owner)
    cli_layer = _cli_layer(inp, text, schema)

    resolved = resolve_new(name=inp.name, presets=inp.preset, cli=cli_layer, packet=packet_layer)

    sources: list[LaunchSource] = []
    if goal_src is not None:
        sources.append(_source("goal", *goal_src, goal or ""))
    _append_text_source(sources, text_owner, resolved.settings.text, inp, base)
    _append_schema_source(sources, schema_owner, resolved.settings.output_schema, inp, base)
    _append_instruction_source(
        sources,
        "base",
        base_owner,
        resolved.base_instructions,
        inp.base_instructions,
        inp.base_file,
    )
    _append_instruction_source(
        sources,
        "developer",
        dev_owner,
        resolved.developer_instructions,
        inp.developer_instructions,
        inp.developer_file,
    )

    would_send = resolved.settings.text is not None and inp.send
    available = _stage_available(resolved, goal, packet)
    stage_plan = resolve_stage_plan(inp.stage, inp.inline, available)
    return ResolvedLaunch(
        resolved=resolved,
        goal=goal,
        text=resolved.settings.text,
        output_schema=resolved.settings.output_schema,
        packet=packet,
        sources=sources,
        unknown_files=list(packet.unknown_files) if packet else [],
        aux_dirs=list(packet.aux_dirs) if packet else [],
        would_send=would_send,
        stage_plan=stage_plan,
    )


def _stage_available(
    resolved: ResolvedNew, goal: str | None, packet: PacketContent | None
) -> frozenset[StagePart]:
    available: set[StagePart] = set()
    if goal is not None:
        available.add("goal")
    if resolved.settings.text is not None:
        available.add("prompt")
    if resolved.settings.output_schema is not None:
        available.add("output_schema")
    if resolved.base_instructions is not None:
        available.add("base")
    if resolved.developer_instructions is not None:
        available.add("developer")
    if packet is not None:
        if packet.has_config:
            available.add("config")
        if "hooks" in packet.aux_dirs:
            available.add("hooks")
        if "codex" in packet.aux_dirs:
            available.add("codex_config")
    return frozenset(available)


def _resolve_goal(
    inp: NewInput, packet: PacketContent | None, base: Path
) -> tuple[str | None, tuple[LaunchOrigin, str | None] | None]:
    if inp.goal is not None and inp.goal_file is not None:
        raise ValidationError("provide --goal or --goal-file, not both")
    if inp.goal is not None:
        return inp.goal, ("inline", None)
    if inp.goal_file is not None:
        path = _resolve_path(inp.goal_file, base)
        return _read_file(path, "--goal-file"), ("file", str(path))
    if packet is not None and packet.goal is not None:
        return packet.goal, ("packet", str(packet.path / "goal.md"))
    return None, None


def _resolve_text(
    inp: NewInput, packet: PacketContent | None, base: Path
) -> tuple[str | None, _Owner]:
    if inp.text is not None and inp.input_file is not None:
        raise ValidationError("provide --text or --input-file, not both")
    if inp.text is not None or inp.input_file is not None:
        return _cli_text(inp, base), "cli"
    if packet is not None and packet.prompt is not None:
        return packet.prompt, "packet"
    return None, "config"


def _cli_text(inp: NewInput, base: Path) -> str | None:
    if inp.text is not None:
        return inp.text
    if inp.input_file is not None:
        return _read_file(_resolve_path(inp.input_file, base), "--input-file")
    return None


def _resolve_output_schema(
    inp: NewInput, packet: PacketContent | None, base: Path
) -> tuple[dict[str, object] | None, _Owner]:
    if inp.output_schema_file is not None:
        path = _resolve_path(inp.output_schema_file, base)
        raw = _read_file(path, "--output-schema-file")
        return parse_output_schema(raw, where="output schema file"), "cli"
    if packet is not None and packet.output_schema is not None:
        return packet.output_schema, "packet"
    return None, "config"


def _instruction_owner(
    inline: str | None, file: str | None, packet: PacketContent | None, slot: str
) -> _Owner:
    if inline is not None or file is not None:
        return "cli"
    packet_value = getattr(packet, slot) if packet is not None else None
    if packet_value is not None:
        return "packet"
    return "config"


def _packet_layer(
    packet: PacketContent | None,
    text_owner: _Owner,
    schema_owner: _Owner,
    base_owner: _Owner,
    dev_owner: _Owner,
) -> NewSettings | None:
    if packet is None:
        return None
    layer = packet.settings.model_copy()
    update: dict[str, object] = {}
    if text_owner == "packet":
        update["text"] = packet.prompt
    if schema_owner == "packet":
        update["output_schema"] = packet.output_schema
    if base_owner == "packet":
        update["base_instructions"] = packet.base
    if dev_owner == "packet":
        update["developer_instructions"] = packet.developer
    return layer.model_copy(update=update)


def _cli_layer(inp: NewInput, text: str | None, schema: dict[str, object] | None) -> NewSettings:
    return NewSettings(
        cwd=inp.cwd,
        permission_profile=inp.permission_profile,
        sandbox=inp.sandbox,
        approval_policy=inp.approval_policy,
        approvals_reviewer=inp.approvals_reviewer,
        model=inp.model,
        model_provider=inp.model_provider,
        effort=inp.effort,
        summary=inp.summary,
        personality=inp.personality,
        service_tier=inp.service_tier,
        ephemeral=inp.ephemeral,
        prefix=inp.prefix,
        text=text,
        base_instructions=inp.base_instructions,
        base_file=inp.base_file,
        developer_instructions=inp.developer_instructions,
        developer_file=inp.developer_file,
        output_schema=schema,
    )


def _append_text_source(
    sources: list[LaunchSource], owner: _Owner, value: str | None, inp: NewInput, base: Path
) -> None:
    if value is None:
        return
    origin: LaunchOrigin
    if owner == "cli":
        origin = "inline" if inp.text is not None else "file"
        path = str(_resolve_path(inp.input_file, base)) if inp.input_file else None
    else:
        origin = owner
        path = None
    sources.append(_source("prompt", origin, path, value))


def _append_schema_source(
    sources: list[LaunchSource],
    owner: _Owner,
    value: dict[str, object] | None,
    inp: NewInput,
    base: Path,
) -> None:
    if value is None:
        return
    origin: LaunchOrigin
    if owner == "cli":
        path = str(_resolve_path(inp.output_schema_file, base)) if inp.output_schema_file else None
        origin = "file"
    else:
        origin = owner
        path = None
    sources.append(_source("output_schema", origin, path, _canonical_json(value)))


def _append_instruction_source(
    sources: list[LaunchSource],
    slot: LaunchSlot,
    owner: _Owner,
    value: str | None,
    inline: str | None,
    file: str | None,
) -> None:
    if value is None:
        return
    origin: LaunchOrigin
    if owner == "cli":
        origin = "inline" if inline is not None else "file"
        path = file
    else:
        origin = owner
        path = None
    sources.append(_source(slot, origin, path, value))


def _source(slot: LaunchSlot, origin: LaunchOrigin, path: str | None, content: str) -> LaunchSource:
    encoded = content.encode("utf-8")
    return LaunchSource(
        slot=slot,
        origin=origin,
        path=path,
        bytes=len(encoded),
        sha256=sha256(encoded).hexdigest(),
    )


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _read_file(path: Path, flag: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValidationError(f"{flag} not found: {path}") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read {flag} {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{flag} {path} is not valid UTF-8: {exc}") from exc


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else (Path.cwd() / expanded).resolve()


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()
