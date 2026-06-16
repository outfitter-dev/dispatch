"""Packet loading + launch-input resolution (packet/file/inline precedence)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outfitter.dispatch.contracts.errors import ValidationError
from outfitter.dispatch.core.launch import resolve_launch
from outfitter.dispatch.core.models import NewInput
from outfitter.dispatch.core.packet import load_packet


def _packet(root: Path) -> Path:
    pkt = root / "packet"
    pkt.mkdir()
    return pkt


def test_load_packet_reads_known_files(tmp_path: Path) -> None:
    pkt = _packet(tmp_path)
    (pkt / "goal.md").write_text("Ship the feature.")
    (pkt / "prompt.md").write_text("Start working now.")
    (pkt / "base.md").write_text("Base rules.")
    (pkt / "developer.md").write_text("Dev rules.")
    (pkt / "output.schema.json").write_text(json.dumps({"type": "object"}))
    (pkt / "dispatch.toml").write_text('sandbox = "workspace-write"\nmodel = "gpt-5.5"\n')

    content = load_packet(pkt)

    assert content.goal == "Ship the feature."
    assert content.prompt == "Start working now."
    assert content.base == "Base rules."
    assert content.developer == "Dev rules."
    assert content.output_schema == {"type": "object"}
    assert content.settings.sandbox == "workspace-write"
    assert content.settings.model == "gpt-5.5"


def test_load_packet_requires_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(ValidationError, match="packet"):
        load_packet(missing)


def test_load_packet_rejects_non_object_schema(tmp_path: Path) -> None:
    pkt = _packet(tmp_path)
    (pkt / "output.schema.json").write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValidationError, match="output"):
        load_packet(pkt)


def test_load_packet_rejects_invalid_schema_json(tmp_path: Path) -> None:
    pkt = _packet(tmp_path)
    (pkt / "output.schema.json").write_text("{not json")
    with pytest.raises(ValidationError, match="output"):
        load_packet(pkt)


def test_load_packet_rejects_unknown_dispatch_toml_keys(tmp_path: Path) -> None:
    pkt = _packet(tmp_path)
    (pkt / "dispatch.toml").write_text('bogus = "value"\n')
    with pytest.raises(ValidationError, match="dispatch"):
        load_packet(pkt)


def test_load_packet_records_unknown_and_aux_entries(tmp_path: Path) -> None:
    pkt = _packet(tmp_path)
    (pkt / "goal.md").write_text("g")
    (pkt / "hooks").mkdir()
    (pkt / "codex").mkdir()
    (pkt / "extra.txt").write_text("noise")

    content = load_packet(pkt)

    assert "extra.txt" in content.unknown_files
    assert "hooks" in content.aux_dirs
    assert "codex" in content.aux_dirs


def test_resolve_launch_uses_packet_content(tmp_path: Path) -> None:
    pkt = _packet(tmp_path)
    (pkt / "goal.md").write_text("Packet goal.")
    (pkt / "prompt.md").write_text("Packet prompt.")
    (pkt / "output.schema.json").write_text(json.dumps({"type": "object"}))

    launch = resolve_launch(NewInput(name="worker", cwd=str(tmp_path), packet=str(pkt)))

    assert launch.goal == "Packet goal."
    assert launch.text == "Packet prompt."
    assert launch.output_schema == {"type": "object"}
    assert launch.would_send is True
    origins = {src.slot: src.origin for src in launch.sources}
    assert origins["goal"] == "packet"
    assert origins["prompt"] == "packet"
    assert origins["output_schema"] == "packet"


def test_resolve_launch_inline_overrides_packet(tmp_path: Path) -> None:
    pkt = _packet(tmp_path)
    (pkt / "goal.md").write_text("Packet goal.")
    (pkt / "prompt.md").write_text("Packet prompt.")

    launch = resolve_launch(
        NewInput(
            name="worker",
            cwd=str(tmp_path),
            packet=str(pkt),
            goal="Inline goal.",
            text="Inline prompt.",
        )
    )

    assert launch.goal == "Inline goal."
    assert launch.text == "Inline prompt."
    origins = {src.slot: src.origin for src in launch.sources}
    assert origins["goal"] == "inline"
    assert origins["prompt"] == "inline"


def test_resolve_launch_reads_explicit_files(tmp_path: Path) -> None:
    goal_file = tmp_path / "g.md"
    goal_file.write_text("File goal.")
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("File prompt.")
    schema_file = tmp_path / "s.json"
    schema_file.write_text(json.dumps({"type": "object", "required": []}))

    launch = resolve_launch(
        NewInput(
            name="worker",
            cwd=str(tmp_path),
            goal_file=str(goal_file),
            input_file=str(prompt_file),
            output_schema_file=str(schema_file),
        )
    )

    assert launch.goal == "File goal."
    assert launch.text == "File prompt."
    assert launch.output_schema == {"type": "object", "required": []}
    origins = {src.slot: src.origin for src in launch.sources}
    assert origins["goal"] == "file"
    assert origins["prompt"] == "file"


def test_resolve_launch_rejects_goal_inline_and_file(tmp_path: Path) -> None:
    goal_file = tmp_path / "g.md"
    goal_file.write_text("x")
    with pytest.raises(ValidationError, match="goal"):
        resolve_launch(
            NewInput(name="w", cwd=str(tmp_path), goal="inline", goal_file=str(goal_file))
        )


def test_resolve_launch_rejects_text_inline_and_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x")
    with pytest.raises(ValidationError, match="input-file"):
        resolve_launch(
            NewInput(name="w", cwd=str(tmp_path), text="inline", input_file=str(prompt_file))
        )


def test_resolve_launch_rejects_missing_input_file(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="input-file"):
        resolve_launch(
            NewInput(name="w", cwd=str(tmp_path), input_file=str(tmp_path / "absent.md"))
        )


def test_resolve_launch_rejects_non_object_schema_file(tmp_path: Path) -> None:
    schema_file = tmp_path / "s.json"
    schema_file.write_text(json.dumps("a string"))
    with pytest.raises(ValidationError, match="output schema"):
        resolve_launch(NewInput(name="w", cwd=str(tmp_path), output_schema_file=str(schema_file)))


def test_resolve_launch_packet_settings_below_cli(tmp_path: Path) -> None:
    pkt = _packet(tmp_path)
    (pkt / "dispatch.toml").write_text('sandbox = "workspace-write"\nmodel = "packet-model"\n')

    launch = resolve_launch(
        NewInput(name="w", cwd=str(tmp_path), packet=str(pkt), model="cli-model")
    )

    # packet provides sandbox; CLI overrides model.
    assert launch.resolved.settings.sandbox == "workspace-write"
    assert launch.resolved.settings.model == "cli-model"


def test_resolve_launch_no_send_when_no_text(tmp_path: Path) -> None:
    launch = resolve_launch(NewInput(name="w", cwd=str(tmp_path), goal="only a goal"))
    assert launch.text is None
    assert launch.would_send is False


def test_resolve_launch_reports_instruction_file_source(tmp_path: Path) -> None:
    base_file = tmp_path / "base.md"
    base_file.write_text("Base instructions body.")

    launch = resolve_launch(NewInput(name="w", cwd=str(tmp_path), base_file=str(base_file)))

    base_src = next(src for src in launch.sources if src.slot == "base")
    assert base_src.origin == "file"
    assert base_src.path == str(base_file)  # absolute path, as forwarded by the CLI
    assert base_src.bytes == len(b"Base instructions body.")


def test_resolve_launch_inlined_stdin_goal_reports_inline_origin(tmp_path: Path) -> None:
    # The CLI inlines `--goal-file -` into `goal` before the daemon resolves, so
    # the reported origin is "inline" with byte/hash provenance over the content.
    launch = resolve_launch(NewInput(name="w", cwd=str(tmp_path), goal="from stdin"))
    goal_src = next(src for src in launch.sources if src.slot == "goal")
    assert goal_src.origin == "inline"
    assert goal_src.path is None
    assert goal_src.bytes == len(b"from stdin")
