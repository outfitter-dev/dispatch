"""Stage-plan resolution and the atomic session-directory writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outfitter.dispatch.contracts.errors import StagingError, ValidationError
from outfitter.dispatch.core.models import StagePart
from outfitter.dispatch.core.staging import (
    StageContent,
    StagePlan,
    resolve_stage_plan,
    stage_session,
)

_AVAILABLE: frozenset[StagePart] = frozenset(
    {"goal", "prompt", "output_schema", "base", "developer", "hooks"}
)


def test_resolve_stage_plan_all_expands_to_available() -> None:
    plan = resolve_stage_plan("all", None, _AVAILABLE)
    # Canonical order, only available parts.
    assert plan.parts == ("goal", "prompt", "output_schema", "base", "developer", "hooks")


def test_resolve_stage_plan_subset_in_canonical_order() -> None:
    plan = resolve_stage_plan("prompt,goal", None, _AVAILABLE)
    assert plan.parts == ("goal", "prompt")


def test_resolve_stage_plan_inline_removes_parts() -> None:
    plan = resolve_stage_plan("all", "prompt,base", _AVAILABLE)
    assert "prompt" not in plan.parts
    assert "base" not in plan.parts
    assert "goal" in plan.parts


def test_resolve_stage_plan_inline_all_stages_nothing() -> None:
    plan = resolve_stage_plan("all", "all", _AVAILABLE)
    assert plan.parts == ()


def test_resolve_stage_plan_none_stages_nothing() -> None:
    assert resolve_stage_plan(None, None, _AVAILABLE).parts == ()


def test_resolve_stage_plan_rejects_unknown_part() -> None:
    with pytest.raises(ValidationError, match="unknown part"):
        resolve_stage_plan("bogus", None, _AVAILABLE)


def test_resolve_stage_plan_rejects_unavailable_explicit_part() -> None:
    with pytest.raises(ValidationError, match="not available"):
        resolve_stage_plan("codex_config", None, _AVAILABLE)


def test_resolve_stage_plan_rejects_all_with_specific() -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        resolve_stage_plan("all,goal", None, _AVAILABLE)


def test_stage_session_writes_tree_and_state(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    content = StageContent(
        goal="Ship it.",
        prompt="Begin work.",
        output_schema={"type": "object"},
    )
    plan = StagePlan(parts=("goal", "prompt", "output_schema"))

    result = stage_session(cwd=cwd, ref="0AB12x", lane_id="lane-1", plan=plan, content=content)

    session = cwd / ".agents" / "sessions" / "0AB12x"
    assert result.session_dir == session
    assert (session / "packet" / "goal.md").read_text() == "Ship it."
    assert (session / "packet" / "prompt.md").read_text() == "Begin work."
    assert json.loads((session / "packet" / "output.schema.json").read_text()) == {"type": "object"}
    assert (session / "scratch").is_dir()
    state = json.loads((session / "state.json").read_text())
    assert state["ref"] == "0AB12x"
    assert state["lane_id"] == "lane-1"
    assert state["launch_state"] == "pending_turn"
    assert {f["part"] for f in state["files"]} == {"goal", "prompt", "output_schema"}
    goal_entry = next(e for e in result.entries if e.part == "goal")
    assert goal_entry.bytes == len(b"Ship it.")
    assert goal_entry.sha256 is not None


def test_stage_session_copies_packet_dirs(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    packet = tmp_path / "packet"
    (packet / "hooks").mkdir(parents=True)
    (packet / "hooks" / "pre.sh").write_text("echo hi")
    content = StageContent(goal="g", packet_path=packet)
    plan = StagePlan(parts=("goal", "hooks"))

    result = stage_session(cwd=cwd, ref="R1", lane_id="L1", plan=plan, content=content)

    staged_hook = result.session_dir / "packet" / "hooks" / "pre.sh"
    assert staged_hook.read_text() == "echo hi"
    hooks_entry = next(e for e in result.entries if e.part == "hooks")
    assert hooks_entry.bytes is None  # directories report no byte count


def test_stage_session_refuses_overwrite(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    existing = cwd / ".agents" / "sessions" / "R1"
    existing.mkdir(parents=True)
    content = StageContent(goal="g")
    plan = StagePlan(parts=("goal",))

    with pytest.raises(StagingError, match="already exists"):
        stage_session(cwd=cwd, ref="R1", lane_id="L1", plan=plan, content=content)


def test_stage_session_atomic_no_partial_on_failure(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    # 'hooks' requested but packet has no hooks dir -> failure mid-write.
    content = StageContent(goal="g", packet_path=tmp_path / "absent-packet")
    plan = StagePlan(parts=("goal", "hooks"))

    with pytest.raises(StagingError):
        stage_session(cwd=cwd, ref="R1", lane_id="L1", plan=plan, content=content)

    # No session dir and no leftover temp dir.
    assert not (cwd / ".agents" / "sessions" / "R1").exists()
    leftovers = list((cwd / ".agents" / "sessions").glob(".staging-*"))
    assert leftovers == []
