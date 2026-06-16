"""Tests for the ergonomic CLI derivation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from outfitter.dispatch.contracts.derive_cli import derive_cli
from outfitter.dispatch.core.ops import REGISTRY

runner = CliRunner()


def test_send_positional_args_and_mode_flags_map_to_send_contract() -> None:
    captured: dict[str, object] = {}

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        captured["op"] = op_id
        captured["params"] = params
        return {"lane": "L1", "op": "send", "accepted": True}

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(app, ["send", "@docs", "hi", "--interject"])

    assert result.exit_code == 0
    assert captured["op"] == "send"
    assert captured["params"] == {
        "lane": "@docs",
        "text": "hi",
        "mode": "interject",
        "intro": False,
    }


def test_send_intro_flag_maps_to_send_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("CODEX_THREAD_ID", "sender-thread")

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        captured["op"] = op_id
        captured["params"] = params
        return {"lane": "L1", "op": "send", "accepted": True}

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(app, ["send", "@docs", "hi", "--intro"])

    assert result.exit_code == 0
    assert captured["op"] == "send"
    assert captured["params"] == {
        "lane": "@docs",
        "text": "hi",
        "mode": "send",
        "intro": True,
        "caller_thread_id": "sender-thread",
    }


def test_send_rejects_multiple_modes() -> None:
    app = derive_cli(REGISTRY, lambda _op, _params: {})

    result = runner.invoke(app, ["send", "@docs", "hi", "--steer", "--context"])

    assert result.exit_code == 2
    assert "choose exactly one send mode" in result.stderr


def test_stop_accepts_positional_or_lane_option() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((op_id, params))
        return {"lane": "L1", "op": "stop", "accepted": True}

    app = derive_cli(REGISTRY, invoke)

    positional = runner.invoke(app, ["stop", "@docs"])
    option = runner.invoke(app, ["stop", "--lane", "@docs"])

    assert positional.exit_code == 0
    assert option.exit_code == 0
    assert calls == [
        ("stop", {"lane": "@docs"}),
        ("stop", {"lane": "@docs"}),
    ]


def test_new_command_maps_repeated_presets_and_no_send() -> None:
    captured: dict[str, object] = {}

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        captured["op"] = op_id
        captured["params"] = params
        return {
            "id": "L1",
            "handle": "@x",
            "source": "own",
            "status": "idle",
            "message_accepted": False,
            "goal_set": False,
            "latest_turn": {"id": None, "status": None, "error": None, "error_at": None},
        }

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(
        app, ["new", "--name", "x", "--preset", "builder", "--preset", "fast", "--no-send"]
    )
    assert result.exit_code == 0
    assert captured["op"] == "new"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["preset"] == ["builder", "fast"]
    assert params["send"] is False


def test_top_level_thread_actions_route_to_lane_contracts() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((op_id, params))
        if op_id == "search":
            return {
                "query": "needle",
                "matches": [],
                "scanned": 0,
                "next_cursor": None,
                "experimental": True,
            }
        return {
            "id": "L1",
            "handle": "@x",
            "managed": True,
            "source": "own",
            "status": "idle",
        }

    app = derive_cli(REGISTRY, invoke)

    renamed = runner.invoke(app, ["rename", "@old", "new"])
    restored = runner.invoke(app, ["restore", "@old"])
    searched = runner.invoke(app, ["search", "needle", "--thread", "@old", "--limit", "5"])

    assert renamed.exit_code == 0
    assert restored.exit_code == 0
    assert searched.exit_code == 0
    assert calls == [
        ("lane-rename", {"old": "@old", "new": "new"}),
        ("restore", {"target": "@old"}),
        (
            "search",
            {
                "query": "needle",
                "lane": "@old",
                "directory": None,
                "repo": None,
                "managed": False,
                "unmanaged": False,
                "archived": False,
                "since": None,
                "until": None,
                "date_field": "updated_at",
                "sort": "updated_at",
                "ascending": False,
                "limit": 5,
                "max_scan": 200,
            },
        ),
    ]


def test_flat_thread_routes_core_commands() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((op_id, params))
        if op_id == "discover":
            return {"sessions": []}
        if op_id == "lane-rename":
            return {
                "id": "L1",
                "handle": "@new",
                "managed": True,
                "source": "own",
                "status": "idle",
            }
        if op_id == "search":
            return {
                "query": "needle",
                "matches": [],
                "scanned": 0,
                "next_cursor": None,
                "experimental": True,
            }
        if op_id == "restore":
            return {
                "id": "L1",
                "handle": "@old",
                "managed": True,
                "source": "own",
                "status": "idle",
            }
        return {"lanes": []}

    app = derive_cli(REGISTRY, invoke)

    managed = runner.invoke(app, ["list"])
    unmanaged = runner.invoke(app, ["list", "--unmanaged", "--limit", "5"])
    attached = runner.invoke(app, ["attach", "thread-1"])
    got = runner.invoke(app, ["get", "@old"])
    tailed = runner.invoke(app, ["tail", "@old"])
    watched = runner.invoke(app, ["watch", "@old", "--limit", "2", "--timeout", "1"])
    synced = runner.invoke(app, ["sync", "@old"])

    assert managed.exit_code == 0
    assert unmanaged.exit_code == 0
    assert attached.exit_code == 0
    assert got.exit_code == 0
    assert tailed.exit_code == 0
    assert watched.exit_code == 0
    assert synced.exit_code == 0
    assert calls == [
        ("roster", {"include_archived": False}),
        ("discover", {"limit": 5}),
        ("attach", {"thread": "thread-1", "sync": False}),
        ("show", {"lane": "@old", "include_transcript": False, "max_items": 20}),
        ("transcript", {"lane": "@old", "limit": 50}),
        ("watch", {"lane": "@old", "limit": 2, "timeout": 1.0}),
        ("sync", {"lane": "@old", "full": False}),
    ]


def test_goal_trigger_and_daemon_groups_replace_hyphen_commands() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((op_id, params))
        if op_id == "goal-set":
            return {"lane": "L1", "goal": None}
        if op_id == "trigger-list":
            return {"triggers": []}
        return {"lanes": 0, "idle": 0, "busy": 0, "triggers": 0, "triggers_enabled": 0}

    app = derive_cli(REGISTRY, invoke)

    goal = runner.invoke(app, ["goal", "set", "@docs", "ship"])
    goal_status = runner.invoke(app, ["goal", "set", "@docs", "--status", "complete"])
    triggers = runner.invoke(app, ["trigger", "list"])
    daemon = runner.invoke(app, ["daemon", "status"])
    legacy = runner.invoke(app, ["trigger-list"])

    assert goal.exit_code == 0
    assert goal_status.exit_code == 0
    assert triggers.exit_code == 0
    assert daemon.exit_code == 0
    assert legacy.exit_code == 2
    assert calls == [
        ("goal-set", {"lane": "@docs", "objective": "ship", "status": None, "token_budget": None}),
        (
            "goal-set",
            {"lane": "@docs", "objective": None, "status": "complete", "token_budget": None},
        ),
        ("trigger-list", {}),
        ("status", {}),
    ]


def test_lane_archive_prompts_for_confirmation() -> None:
    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        return {
            "id": "L1",
            "handle": "@x",
            "managed": True,
            "source": "own",
            "status": "archived",
        }

    app = derive_cli(REGISTRY, invoke)
    declined = runner.invoke(app, ["archive", "L1"], input="n\n")
    assert declined.exit_code != 0
    confirmed = runner.invoke(app, ["archive", "L1"], input="y\n")
    assert confirmed.exit_code == 0


def test_destroy_ops_support_explicit_noninteractive_confirmation() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((op_id, params))
        return {
            "id": "L1",
            "handle": "@x",
            "managed": True,
            "source": "own",
            "status": "archived",
        }

    app = derive_cli(REGISTRY, invoke)
    refused = runner.invoke(app, ["archive", "L1", "--no-interactive", "--json"])
    confirmed = runner.invoke(app, ["archive", "L1", "--yes", "--no-interactive", "--json"])

    assert refused.exit_code == 2
    assert "requires --yes" in refused.stderr
    assert confirmed.exit_code == 0
    assert calls == [("archive", {"target": "L1"})]


def test_json_destroy_prompt_does_not_pollute_stdout() -> None:
    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        return {
            "id": "L1",
            "handle": "@x",
            "managed": True,
            "source": "own",
            "status": "archived",
        }

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(app, ["archive", "L1", "--json"], input="y\n")

    assert result.exit_code == 0
    assert "Run destroy op" not in result.stdout
    payload = result.stdout[result.stdout.index("{") :]
    assert json.loads(payload) == {
        "id": "L1",
        "handle": "@x",
        "managed": True,
        "source": "own",
        "status": "archived",
    }


def test_schema_command_prints_derived_schema_without_daemon() -> None:
    app = derive_cli(REGISTRY, lambda _op, _params: {})

    result = runner.invoke(app, ["schema", "send"])

    assert result.exit_code == 0
    assert '"op": "send"' in result.output
    assert '"mode"' in result.output
    assert "caller_thread_id" not in result.output
    assert "reserved for durable queued delivery" not in result.output


def test_schema_command_stays_plain_json_when_color_is_forced() -> None:
    app = derive_cli(REGISTRY, lambda _op, _params: {})

    result = runner.invoke(app, ["schema", "send"], env={"FORCE_COLOR": "3"})

    assert result.exit_code == 0
    assert "\x1b[" not in result.output
    assert '"op": "send"' in result.output


def test_schema_command_resolves_composed_cli_routes() -> None:
    app = derive_cli(REGISTRY, lambda _op, _params: {})

    unmanaged = runner.invoke(app, ["schema", "list --unmanaged"])
    search = runner.invoke(app, ["schema", "search"])
    attach = runner.invoke(app, ["schema", "attach"])
    get = runner.invoke(app, ["schema", "get"])
    tail = runner.invoke(app, ["schema", "tail"])
    watch = runner.invoke(app, ["schema", "watch"])
    sync = runner.invoke(app, ["schema", "sync"])
    follow = runner.invoke(app, ["schema", "tail --follow"])
    archive = runner.invoke(app, ["schema", "archive"])
    restore = runner.invoke(app, ["schema", "restore"])

    assert unmanaged.exit_code == 0
    assert '"op": "discover"' in unmanaged.output
    assert search.exit_code == 0
    assert '"op": "search"' in search.output
    assert attach.exit_code == 0
    assert '"op": "attach"' in attach.output
    assert get.exit_code == 0
    assert '"op": "show"' in get.output
    assert tail.exit_code == 0
    assert '"op": "transcript"' in tail.output
    assert watch.exit_code == 0
    assert '"op": "watch"' in watch.output
    assert sync.exit_code == 0
    assert '"op": "sync"' in sync.output
    assert follow.exit_code == 2
    assert archive.exit_code == 0
    assert '"op": "archive"' in archive.output
    assert restore.exit_code == 0
    assert '"op": "restore"' in restore.output


def test_schema_command_unknown_target_is_usage_error() -> None:
    app = derive_cli(REGISTRY, lambda _op, _params: {})

    result = runner.invoke(app, ["schema", "lane fly"])

    assert result.exit_code == 2
    assert "unknown command or op for schema" in result.stderr
    assert "Traceback" not in result.output


def test_error_exit_code_propagates() -> None:
    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        raise typer.Exit(code=4)

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(app, ["get", "ghost"])
    assert result.exit_code == 4


def _capture_invoke(
    captured: dict[str, object],
) -> Callable[[str, dict[str, object]], dict[str, object]]:
    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        captured["op"] = op_id
        captured["params"] = params
        return {}

    return invoke


def test_new_routes_to_new_op_by_default() -> None:
    captured: dict[str, object] = {}
    app = derive_cli(REGISTRY, _capture_invoke(captured))
    result = runner.invoke(app, ["new", "--name", "worker", "--cwd", "/work"])
    assert result.exit_code == 0, result.output
    assert captured["op"] == "new"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["name"] == "worker"


def test_new_dry_run_routes_to_new_plan_op() -> None:
    captured: dict[str, object] = {}
    app = derive_cli(REGISTRY, _capture_invoke(captured))
    result = runner.invoke(app, ["new", "--name", "worker", "--cwd", "/work", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert captured["op"] == "new-plan"
    params = captured["params"]
    assert isinstance(params, dict)
    assert "dry_run" not in params  # routing flag, not an op field


def test_new_goal_file_dash_reads_stdin_into_inline_goal() -> None:
    captured: dict[str, object] = {}
    app = derive_cli(REGISTRY, _capture_invoke(captured))
    result = runner.invoke(
        app, ["new", "--name", "worker", "--goal-file", "-"], input="stdin goal text"
    )
    assert result.exit_code == 0, result.output
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["goal"] == "stdin goal text"
    assert params["goal_file"] is None


def test_new_rejects_two_stdin_consumers() -> None:
    captured: dict[str, object] = {}
    app = derive_cli(REGISTRY, _capture_invoke(captured))
    result = runner.invoke(
        app, ["new", "--name", "worker", "--goal-file", "-", "--input-file", "-"], input="x"
    )
    assert result.exit_code == 2
    assert "stdin" in result.output
    assert "op" not in captured  # never reached the daemon


def test_new_rejects_stdin_conflicting_with_inline() -> None:
    captured: dict[str, object] = {}
    app = derive_cli(REGISTRY, _capture_invoke(captured))
    result = runner.invoke(
        app, ["new", "--name", "worker", "--goal", "inline", "--goal-file", "-"], input="x"
    )
    assert result.exit_code == 2
    assert "op" not in captured


def test_new_absolutizes_packet_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    app = derive_cli(REGISTRY, _capture_invoke(captured))
    (tmp_path / "pkt").mkdir()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "--name", "worker", "--packet", "pkt"])
    assert result.exit_code == 0, result.output
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["packet"] == str((tmp_path / "pkt").resolve())


def test_new_stage_and_inline_pass_through() -> None:
    captured: dict[str, object] = {}
    app = derive_cli(REGISTRY, _capture_invoke(captured))
    result = runner.invoke(
        app, ["new", "--name", "w", "--cwd", "/work", "--stage", "all", "--inline", "prompt"]
    )
    assert result.exit_code == 0, result.output
    assert captured["op"] == "new"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["stage"] == "all"
    assert params["inline"] == "prompt"


def test_new_json_output_includes_staged_summary() -> None:
    staged_payload = {
        "id": "L1",
        "ref": "0AB12x",
        "handle": "@x",
        "source": "own",
        "status": "idle",
        "cwd": "/work",
        "message_accepted": True,
        "goal_set": False,
        "staged": {
            "parts": ["goal", "prompt"],
            "session_dir": "/work/.agents/sessions/0AB12x",
            "files": [],
        },
        "latest_turn": {"id": None, "status": None, "error": None, "error_at": None},
    }

    def invoke(op_id: str, params: dict[str, object]) -> dict[str, object]:
        return staged_payload

    app = derive_cli(REGISTRY, invoke)
    result = runner.invoke(app, ["new", "--name", "x", "--cwd", "/work", "--json"])
    assert result.exit_code == 0, result.output
    rendered = json.loads(result.output)
    assert rendered["staged"]["parts"] == ["goal", "prompt"]
    assert rendered["staged"]["session_dir"] == "/work/.agents/sessions/0AB12x"
