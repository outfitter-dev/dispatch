"""Run live Dispatch/Codex scenario fixtures.

Scenarios exercise the public Dispatch CLI against an isolated ``DISPATCH_HOME``
and ``CODEX_HOME``. They are intentionally outside ``just check`` because they
use real Codex auth/model calls. Use them before releases or when validating
agent-level workflows end to end.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Status = Literal["pending", "started", "completed", "failed"]
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ScenarioLane:
    alias: str
    name: str
    prompt: str
    expect_contains: str
    sandbox: str | None
    approval_policy: str | None
    expect_file: str | None
    expect_file_contains: str | None
    expect_tool: str | None


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    timeout_seconds: float
    poll_seconds: float
    preferred_model: str | None
    allow_model_fallback: bool
    effort: str
    parallel: bool
    owned_interactive_requests: str | None
    verify_bounded_sync: bool
    unmanaged_sync: bool
    lanes: list[ScenarioLane]


@dataclass
class LaneRun:
    alias: str
    id: str
    handle: str | None
    expect_contains: str
    expect_file: str | None
    expect_file_contains: str | None
    expect_tool: str | None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    scenario = _load_scenario(args.scenario)
    if args.dry_run:
        _print_plan(scenario, args)
        return 0
    runner = ScenarioRunner(scenario=scenario, args=args)
    return runner.run()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    raw_args = sys.argv[1:] if argv is None else argv
    if raw_args and raw_args[0] == "--":
        raw_args = raw_args[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="TOML scenario fixture to run.")
    parser.add_argument(
        "--dispatch-bin",
        default=None,
        help=(
            "Command used to run dispatch, shell-split. "
            "Defaults to `uv run --project <repo> dispatch`."
        ),
    )
    parser.add_argument(
        "--keep-home",
        action="store_true",
        help="Keep temporary homes/work dir for debugging.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the scenario plan without starting Dispatch/Codex.",
    )
    return parser.parse_args(raw_args)


def _load_scenario(path: Path) -> Scenario:
    data = tomllib.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a TOML table")
    raw_lanes = data.get("lanes")
    if not isinstance(raw_lanes, list) or not raw_lanes:
        raise SystemExit(f"{path} must define at least one [[lanes]] entry")
    lanes = [_load_lane(index, raw) for index, raw in enumerate(raw_lanes, start=1)]
    aliases = [lane.alias for lane in lanes]
    if len(set(aliases)) != len(aliases):
        raise SystemExit(f"{path} has duplicate lane aliases")
    return Scenario(
        name=_string(data, "name", fallback=path.stem),
        description=_string(data, "description", fallback=""),
        timeout_seconds=float(data.get("timeout_seconds", 120.0)),
        poll_seconds=float(data.get("poll_seconds", 1.0)),
        preferred_model=_optional_string(data, "preferred_model"),
        allow_model_fallback=bool(data.get("allow_model_fallback", True)),
        effort=_string(data, "effort", fallback="low"),
        parallel=bool(data.get("parallel", True)),
        owned_interactive_requests=_optional_string(data, "owned_interactive_requests"),
        verify_bounded_sync=bool(data.get("verify_bounded_sync", False)),
        unmanaged_sync=bool(data.get("unmanaged_sync", False)),
        lanes=lanes,
    )


def _load_lane(index: int, raw: object) -> ScenarioLane:
    if not isinstance(raw, dict):
        raise SystemExit(f"lane #{index} must be a TOML table")
    return ScenarioLane(
        alias=_string(raw, "alias"),
        name=_string(raw, "name"),
        prompt=_string(raw, "prompt"),
        expect_contains=_string(raw, "expect_contains"),
        sandbox=_optional_string(raw, "sandbox"),
        approval_policy=_optional_string(raw, "approval_policy"),
        expect_file=_optional_string(raw, "expect_file"),
        expect_file_contains=_optional_string(raw, "expect_file_contains"),
        expect_tool=_optional_string(raw, "expect_tool"),
    )


def _string(data: dict[str, object], key: str, *, fallback: str | None = None) -> str:
    value = data.get(key, fallback)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"missing or invalid string field: {key}")
    return value


def _optional_string(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SystemExit(f"invalid string field: {key}")
    return value


def _print_plan(scenario: Scenario, args: argparse.Namespace) -> None:
    print(f"scenario={scenario.name}")
    if scenario.description:
        print(f"description={scenario.description}")
    print(f"dispatch_bin={shlex.join(_dispatch_cmd(args))}")
    print(f"preferred_model={scenario.preferred_model or '<codex default>'}")
    print(f"effort={scenario.effort}")
    print(f"parallel={scenario.parallel}")
    print(f"owned_interactive_requests={scenario.owned_interactive_requests or '<default>'}")
    print(f"verify_bounded_sync={scenario.verify_bounded_sync}")
    print(f"unmanaged_sync={scenario.unmanaged_sync}")
    for lane in scenario.lanes:
        print(f"lane {lane.alias}: name={lane.name!r} expect={lane.expect_contains!r}")


class ScenarioRunner:
    def __init__(self, *, scenario: Scenario, args: argparse.Namespace) -> None:
        self.scenario = scenario
        self.args = args
        self.dispatch_cmd = _dispatch_cmd(args)
        self.dispatch_home = _mktemp("dispatch-scenario-home.")
        self.codex_home = _mktemp("dispatch-scenario-codex.")
        self.work_dir = _mktemp("dispatch-scenario-work.")
        self.env = os.environ.copy()
        self.env["DISPATCH_HOME"] = str(self.dispatch_home)
        self.env["CODEX_HOME"] = str(self.codex_home)

    def run(self) -> int:
        print(f"scenario={self.scenario.name}")
        print(f"DISPATCH_HOME={self.dispatch_home}")
        print(f"CODEX_HOME={self.codex_home}")
        print(f"work_dir={self.work_dir}")
        try:
            self._prepare_codex_home()
            self._prepare_dispatch_home()
            self._prepare_work_dir()
            if self.scenario.unmanaged_sync:
                lanes = self._start_unmanaged_lanes(self.scenario.preferred_model)
                self._dispatch_json(["up", "--json"])
            else:
                self._dispatch_json(["up", "--json"])
                model = self._resolve_model()
                lanes = self._start_lanes(model)
                self._assert_list_contains(lanes)
            for lane in lanes:
                if not self.scenario.unmanaged_sync:
                    self._wait_for_completion(lane)
                self._assert_bounded_sync(lane)
                if self.scenario.unmanaged_sync:
                    self._assert_list_contains([lane])
                self._assert_tail_contains(lane)
                self._assert_query_contains(lane)
                self._assert_expected_file(lane)
            self._dispatch_json(["down", "--json"])
            print("scenario passed")
            return 0
        finally:
            self._dispatch(["down", "--json"], check=False)
            if not self.args.keep_home:
                shutil.rmtree(self.dispatch_home, ignore_errors=True)
                shutil.rmtree(self.codex_home, ignore_errors=True)
                shutil.rmtree(self.work_dir, ignore_errors=True)
            else:
                print("kept temporary homes for debugging")

    def _prepare_codex_home(self) -> None:
        auth = Path.home() / ".codex" / "auth.json"
        if not shutil.which("codex"):
            raise SystemExit("codex binary not on PATH; cannot run live scenario")
        if not auth.exists():
            raise SystemExit("no ~/.codex/auth.json; cannot run live scenario")
        shutil.copy2(auth, self.codex_home / "auth.json")

    def _prepare_dispatch_home(self) -> None:
        mode = self.scenario.owned_interactive_requests
        if mode is None:
            return
        if mode not in {"attention", "deny", "permissive"}:
            raise SystemExit(f"invalid owned_interactive_requests: {mode}")
        (self.dispatch_home / "config.toml").write_text(
            f'[policy]\nowned_interactive_requests = "{mode}"\n'
        )

    def _prepare_work_dir(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.work_dir, check=True)

    def _resolve_model(self) -> str | None:
        models = self._dispatch_json(["models", "--json"])
        available: set[str] = set()
        for item in _list(models.get("models")):
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str):
                available.add(model_id)
        preferred = self.scenario.preferred_model
        if preferred is None:
            return None
        if preferred in available:
            print(f"model={preferred}")
            return preferred
        if self.scenario.allow_model_fallback:
            print(f"model={preferred} unavailable; falling back to Codex default")
            return None
        raise SystemExit(
            f"preferred model {preferred!r} is unavailable; available: {sorted(available)}"
        )

    def _start_lanes(self, model: str | None) -> list[LaneRun]:
        if self.scenario.parallel:
            return [self._start_lane(lane, model) for lane in self.scenario.lanes]
        runs: list[LaneRun] = []
        for lane in self.scenario.lanes:
            run = self._start_lane(lane, model)
            self._wait_for_completion(run)
            runs.append(run)
        return runs

    def _start_unmanaged_lanes(self, model: str | None) -> list[LaneRun]:
        return [self._start_unmanaged_lane(lane, model) for lane in self.scenario.lanes]

    def _start_unmanaged_lane(self, lane: ScenarioLane, model: str | None) -> LaneRun:
        base = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            lane.sandbox or "read-only",
            "--cd",
            str(self.work_dir),
            "--config",
            f'model_reasoning_effort="{self.scenario.effort}"',
        ]
        command = [*base]
        if model is not None:
            command.extend(["--model", model])
        command.append(lane.prompt)
        result = subprocess.run(
            command,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=self.scenario.timeout_seconds,
            check=False,
        )
        if result.returncode != 0 and model is not None and self.scenario.allow_model_fallback:
            command = [*base, lane.prompt]
            result = subprocess.run(
                command,
                env=self.env,
                text=True,
                capture_output=True,
                timeout=self.scenario.timeout_seconds,
                check=False,
            )
        if result.returncode != 0:
            raise SystemExit(
                f"codex exec failed for {lane.alias}\nstdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        thread_id: str | None = None
        for raw in result.stdout.splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "thread.started":
                candidate = event.get("thread_id")
                if isinstance(candidate, str):
                    thread_id = candidate
                    break
        if thread_id is None:
            raise SystemExit(f"codex exec returned no thread.started event: {result.stdout}")
        print(f"started unmanaged {lane.alias}: id={thread_id}")
        return LaneRun(
            alias=lane.alias,
            id=thread_id,
            handle=None,
            expect_contains=lane.expect_contains,
            expect_file=lane.expect_file,
            expect_file_contains=lane.expect_file_contains,
            expect_tool=lane.expect_tool,
        )

    def _start_lane(self, lane: ScenarioLane, model: str | None) -> LaneRun:
        args = [
            "new",
            "--name",
            lane.name,
            "--cwd",
            str(self.work_dir),
            "--text",
            lane.prompt,
            "--effort",
            self.scenario.effort,
            "--no-ephemeral",
            "--json",
        ]
        if model is not None:
            args.extend(["--model", model])
        if lane.sandbox is not None:
            args.extend(["--sandbox", lane.sandbox])
        if lane.approval_policy is not None:
            args.extend(["--approval-policy", lane.approval_policy])
        out = self._dispatch_json(args, timeout=self.scenario.timeout_seconds)
        lane_id = out.get("id")
        if not isinstance(lane_id, str):
            raise SystemExit(f"new did not return lane id: {out}")
        if out.get("message_accepted") is not True:
            raise SystemExit(f"new did not accept initial message: {out}")
        handle = out.get("handle")
        print(f"started {lane.alias}: id={lane_id} handle={handle}")
        return LaneRun(
            alias=lane.alias,
            id=lane_id,
            handle=handle if isinstance(handle, str) else None,
            expect_contains=lane.expect_contains,
            expect_file=lane.expect_file,
            expect_file_contains=lane.expect_file_contains,
            expect_tool=lane.expect_tool,
        )

    def _assert_list_contains(self, lanes: list[LaneRun]) -> None:
        out = self._dispatch_json(["list", "--json"])
        items = _list(out.get("lanes"))
        ids = {item.get("id") for item in items if isinstance(item, dict)}
        missing = [lane.id for lane in lanes if lane.id not in ids]
        if missing:
            raise SystemExit(f"list did not include scenario lanes: {missing}; got {ids}")

    def _wait_for_completion(self, lane: LaneRun) -> None:
        deadline = time.monotonic() + self.scenario.timeout_seconds
        last_status: Status | None = None
        while time.monotonic() < deadline:
            detail = self._dispatch_json(
                ["get", lane.id, "--include-transcript", "--json"],
                timeout=min(30.0, self.scenario.timeout_seconds),
            )
            latest_turn = detail.get("latest_turn")
            status = latest_turn.get("status") if isinstance(latest_turn, dict) else None
            if status != last_status:
                print(f"{lane.alias}: latest_turn={status}")
                last_status = status if status in {"started", "completed", "failed"} else None
            if status == "completed":
                return
            if status == "failed":
                raise SystemExit(f"{lane.alias} failed: {latest_turn}")
            time.sleep(self.scenario.poll_seconds)
        raise SystemExit(f"{lane.alias} did not complete within {self.scenario.timeout_seconds}s")

    def _assert_tail_contains(self, lane: LaneRun) -> None:
        out = self._dispatch_json(["tail", lane.id, "--limit", "20", "--json"])
        haystack = json.dumps(out, sort_keys=True).lower()
        needle = lane.expect_contains.lower()
        if needle not in haystack:
            raise SystemExit(f"{lane.alias} transcript missing {needle!r}: {out}")

    def _assert_expected_file(self, lane: LaneRun) -> None:
        if lane.expect_file is None:
            return
        path = self.work_dir / lane.expect_file
        if not path.is_file():
            raise SystemExit(f"{lane.alias} expected file was not created: {path}")
        if lane.expect_file_contains is not None:
            contents = path.read_text()
            if lane.expect_file_contains not in contents:
                raise SystemExit(
                    f"{lane.alias} expected {lane.expect_file_contains!r} in {path}: {contents!r}"
                )

    def _assert_bounded_sync(self, lane: LaneRun) -> None:
        if not self.scenario.verify_bounded_sync:
            return
        sync_args = [
            "sync",
            lane.id,
            "--max-turns",
            "2",
            "--max-items",
            "20",
            "--max-bytes",
            "262144",
            "--max-seconds",
            "10",
            "--json",
        ]
        first = self._assert_sync_bounds(lane, self._dispatch_json(sync_args))
        if first.get("turns_indexed") == 0 or first.get("items_indexed") == 0:
            raise SystemExit(f"{lane.alias} sync did not index unmanaged history: {first}")
        matches_before = self._bounded_sync_matches(lane)
        second = self._assert_sync_bounds(lane, self._dispatch_json(sync_args))
        matches_after = self._bounded_sync_matches(lane)
        before_ids = {match.get("item_id") for match in matches_before}
        after_ids = {match.get("item_id") for match in matches_after}
        if not before_ids or before_ids != after_ids:
            raise SystemExit(
                f"{lane.alias} bounded sync did not preserve indexed content: "
                f"before={matches_before}, after={matches_after}"
            )
        if (
            first.get("observation_enabled") is not True
            or second.get("observation_enabled") is not True
        ):
            raise SystemExit(f"{lane.alias} sync did not persist live observation")

    def _assert_sync_bounds(self, lane: LaneRun, out: dict[str, object]) -> dict[str, object]:
        sync = out.get("sync")
        if not isinstance(sync, dict):
            raise SystemExit(f"{lane.alias} sync returned no sync state: {out}")
        capability = sync.get("history_capability")
        if capability not in {"supported", "turn-page-fallback"}:
            raise SystemExit(f"{lane.alias} sync did not establish bounded history: {sync}")
        if not isinstance(sync.get("pages_scanned"), int):
            raise SystemExit(f"{lane.alias} sync omitted page metrics: {sync}")
        turns = sync.get("turns_indexed")
        items = sync.get("items_indexed")
        if not isinstance(turns, int) or turns > 2:
            raise SystemExit(f"{lane.alias} sync exceeded its turn bound: {sync}")
        if not isinstance(items, int) or items > 20:
            raise SystemExit(f"{lane.alias} sync exceeded its item bound: {sync}")
        if not isinstance(sync.get("truncated"), bool):
            raise SystemExit(f"{lane.alias} sync omitted truncation state: {sync}")
        return sync

    def _bounded_sync_matches(self, lane: LaneRun) -> list[dict[str, object]]:
        out = self._dispatch_json(["query", lane.expect_contains, "--lane", lane.id, "--json"])
        matches = out.get("matches")
        if not isinstance(matches, list) or not all(isinstance(match, dict) for match in matches):
            raise SystemExit(f"{lane.alias} query returned invalid matches: {out}")
        return matches

    def _assert_query_contains(self, lane: LaneRun) -> None:
        if lane.expect_tool is None:
            return
        out = self._dispatch_json(
            ["query", "--lane", lane.id, "--tool", lane.expect_tool, "--json"]
        )
        if not _list(out.get("matches")):
            raise SystemExit(
                f"{lane.alias} local query did not index tool {lane.expect_tool!r}: {out}"
            )

    def _dispatch_json(self, args: list[str], *, timeout: float = 90.0) -> dict[str, Any]:
        result = self._dispatch(args, timeout=timeout)
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"dispatch {' '.join(args)} did not produce JSON\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            ) from exc
        if not isinstance(parsed, dict):
            raise SystemExit(f"dispatch {' '.join(args)} produced non-object JSON")
        return parsed

    def _dispatch(
        self,
        args: list[str],
        *,
        timeout: float = 90.0,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*self.dispatch_cmd, *args],
            env=self.env,
            cwd=self.work_dir,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            raise SystemExit(
                f"dispatch {' '.join(args)} failed with {result.returncode}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _dispatch_cmd(args: argparse.Namespace) -> list[str]:
    if args.dispatch_bin is not None:
        return shlex.split(args.dispatch_bin)
    return ["uv", "run", "--project", str(REPO_ROOT), "dispatch"]


def _mktemp(prefix: str) -> Path:
    root = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


if __name__ == "__main__":
    sys.exit(main())
