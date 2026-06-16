"""Workspace preflight for ``dispatch new``.

Workspace launch is deliberately smaller than a hook framework. Dispatch can
discover repo-local Codex environment metadata, optionally run a trusted setup
script, and report the exact cwd it will pass to App Server. It does not infer
domain workflows or execute packet-local hooks.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from outfitter.dispatch.config import RuntimePolicy, worktree_root_path
from outfitter.dispatch.contracts.errors import ValidationError

from .models import (
    WorkspaceEnvironmentView,
    WorkspaceSetupMode,
    WorkspaceSetupView,
    WorkspaceView,
    WorktreeMode,
    WorktreeView,
)
from .new_config import WorkspaceConfig

_ENV_PATH = Path(".codex") / "environments" / "environment.toml"
_TOP_LEVEL_KEYS = {"version", "name", "setup", "cleanup"}
_OUTPUT_TAIL_CHARS = 4000
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

WorkspaceState = Literal["disabled", "not_found", "discovered", "setup_completed"]


@dataclass(frozen=True)
class WorkspaceResolution:
    effective_cwd: Path
    view: WorkspaceView
    setup_script: str | None


@dataclass(frozen=True)
class WorkspaceOptions:
    mode: str
    resolved_mode: str
    worktree: WorktreeMode
    worktree_path: str | None
    worktree_branch: str | None
    worktree_base: str | None


def plan_workspace(
    *,
    cwd: Path,
    name: str,
    requested: str | None,
    setup: WorkspaceSetupMode,
    worktree: WorktreeMode | None,
    worktree_path: str | None,
    worktree_branch: str | None,
    worktree_base: str | None,
    config: WorkspaceConfig,
    policy: RuntimePolicy,
) -> WorkspaceResolution:
    """Resolve workspace metadata without running setup."""
    options = _resolve_options(
        requested=requested,
        config=config,
        worktree=worktree,
        worktree_path=worktree_path,
        worktree_branch=worktree_branch,
        worktree_base=worktree_base,
    )
    input_cwd = _absolute(cwd)
    source_repo = _find_repo_root(input_cwd)
    worktree_view, effective_cwd = _plan_worktree(
        mode=options.worktree,
        input_cwd=input_cwd,
        source_repo=source_repo,
        name=name,
        path=options.worktree_path,
        branch=options.worktree_branch,
        base=options.worktree_base,
    )
    if options.resolved_mode == "none":
        return WorkspaceResolution(
            effective_cwd=effective_cwd,
            view=_view(
                mode=options.mode,
                resolved_mode=options.resolved_mode,
                state="disabled",
                input_cwd=input_cwd,
                repo_root=source_repo,
                effective_cwd=effective_cwd,
                setup_view=WorkspaceSetupView(policy="not_requested", ran=False),
                worktree_view=worktree_view,
            ),
            setup_script=None,
        )

    repo_root = _find_repo_root(effective_cwd) or source_repo
    env_file = _find_environment_file(effective_cwd, repo_root)
    if env_file is None:
        return WorkspaceResolution(
            effective_cwd=effective_cwd,
            view=_view(
                mode=options.mode,
                resolved_mode=options.resolved_mode,
                state="not_found",
                input_cwd=input_cwd,
                repo_root=repo_root,
                effective_cwd=effective_cwd,
                setup_view=WorkspaceSetupView(policy="not_found", ran=False),
                worktree_view=worktree_view,
            ),
            setup_script=None,
        )

    environment = _load_environment(env_file)
    setup_policy = _setup_policy(setup, policy, has_script=environment.setup_script is not None)
    env_effective_cwd = (
        effective_cwd if options.worktree == "create" else repo_root or effective_cwd
    )
    return WorkspaceResolution(
        effective_cwd=env_effective_cwd,
        view=_view(
            mode=options.mode,
            resolved_mode=options.resolved_mode,
            state="discovered",
            input_cwd=input_cwd,
            repo_root=repo_root,
            effective_cwd=env_effective_cwd,
            environment_file=env_file,
            environment=environment,
            setup_view=WorkspaceSetupView(policy=setup_policy, ran=False),
            worktree_view=worktree_view,
        ),
        setup_script=environment.setup_script,
    )


async def prepare_workspace(
    *,
    cwd: Path,
    name: str,
    requested: str | None,
    setup: WorkspaceSetupMode,
    worktree: WorktreeMode | None,
    worktree_path: str | None,
    worktree_branch: str | None,
    worktree_base: str | None,
    config: WorkspaceConfig,
    policy: RuntimePolicy,
) -> WorkspaceResolution:
    """Resolve workspace metadata and run setup when trusted/explicitly requested."""
    source_cwd = _absolute(cwd)
    source_repo = _find_repo_root(source_cwd)
    options = _resolve_options(
        requested=requested,
        config=config,
        worktree=worktree,
        worktree_path=worktree_path,
        worktree_branch=worktree_branch,
        worktree_base=worktree_base,
    )
    worktree_view, effective_cwd = await _prepare_worktree(
        mode=options.worktree,
        input_cwd=source_cwd,
        source_repo=source_repo,
        name=name,
        path=options.worktree_path,
        branch=options.worktree_branch,
        base=options.worktree_base,
    )
    planned = plan_workspace(
        cwd=effective_cwd,
        name=name,
        requested=options.mode,
        setup=setup,
        worktree="none",
        worktree_path=None,
        worktree_branch=None,
        worktree_base=None,
        config=config,
        policy=policy,
    )
    planned = WorkspaceResolution(
        effective_cwd=planned.effective_cwd,
        view=planned.view.model_copy(
            update={"input_cwd": str(source_cwd), "worktree": worktree_view}
        ),
        setup_script=planned.setup_script,
    )
    if planned.setup_script is None or planned.view.setup.policy not in {"explicit", "trusted"}:
        return planned

    setup_result = await _run_setup(
        script=planned.setup_script,
        cwd=Path(planned.view.repo_root or planned.effective_cwd),
        policy_name=planned.view.setup.policy,
        timeout_seconds=policy.workspace_setup_timeout_seconds,
    )
    view = planned.view.model_copy(update={"state": "setup_completed", "setup": setup_result})
    return WorkspaceResolution(
        effective_cwd=planned.effective_cwd,
        view=view,
        setup_script=planned.setup_script,
    )


def _resolve_options(
    *,
    requested: str | None,
    config: WorkspaceConfig,
    worktree: WorktreeMode | None,
    worktree_path: str | None,
    worktree_branch: str | None,
    worktree_base: str | None,
) -> WorkspaceOptions:
    mode = requested or config.default or "none"
    config_worktree = _validated_worktree(config.worktree)
    config_path = config.worktree_path
    config_branch = config.worktree_branch
    config_base = config.worktree_base
    if mode in {"none", "auto"}:
        resolved_mode = mode
    else:
        preset = config.presets.get(mode)
        if preset is None:
            raise ValidationError(f"unknown workspace preset {mode!r}")
        if preset.mode not in {"none", "auto"}:
            raise ValidationError(f"workspace preset {mode!r} has unsupported mode {preset.mode!r}")
        resolved_mode = preset.mode
        config_worktree = _validated_worktree(preset.worktree) or config_worktree
        config_path = preset.worktree_path or config_path
        config_branch = preset.worktree_branch or config_branch
        config_base = preset.worktree_base or config_base
    return WorkspaceOptions(
        mode=mode,
        resolved_mode=resolved_mode,
        worktree=worktree or config_worktree or "none",
        worktree_path=worktree_path or config_path,
        worktree_branch=worktree_branch or config_branch,
        worktree_base=worktree_base or config_base,
    )


def _validated_worktree(value: str | None) -> WorktreeMode | None:
    if value is None:
        return None
    if value not in {"none", "create"}:
        raise ValidationError(f"unsupported workspace worktree mode {value!r}")
    return cast(WorktreeMode, value)


def _setup_policy(setup: WorkspaceSetupMode, policy: RuntimePolicy, *, has_script: bool) -> str:
    if not has_script:
        return "no_script"
    if setup == "skip":
        return "skipped"
    if setup == "run":
        return "explicit"
    if policy.allow_workspace_setup:
        return "trusted"
    return "not_allowed"


def _plan_worktree(
    *,
    mode: WorktreeMode,
    input_cwd: Path,
    source_repo: Path | None,
    name: str,
    path: str | None,
    branch: str | None,
    base: str | None,
) -> tuple[WorktreeView, Path]:
    if mode == "none":
        return WorktreeView(mode="none", state="disabled"), input_cwd
    if source_repo is None:
        raise ValidationError("--worktree create requires --cwd inside a git repository")
    worktree_path, branch_name, base_ref = _worktree_values(
        source_repo=source_repo, name=name, path=path, branch=branch, base=base
    )
    head = _git(["rev-parse", "--short", base_ref], cwd=source_repo)
    _check_branch_available(source_repo, branch_name)
    return (
        WorktreeView(
            mode="create",
            state="planned",
            path=str(worktree_path),
            branch=branch_name,
            base=base_ref,
            head=head,
            source_repo=str(source_repo),
            created=False,
        ),
        worktree_path,
    )


async def _prepare_worktree(
    *,
    mode: WorktreeMode,
    input_cwd: Path,
    source_repo: Path | None,
    name: str,
    path: str | None,
    branch: str | None,
    base: str | None,
) -> tuple[WorktreeView, Path]:
    if mode == "none":
        return WorktreeView(mode="none", state="disabled"), input_cwd
    if source_repo is None:
        raise ValidationError("--worktree create requires --cwd inside a git repository")
    return await asyncio.to_thread(
        _create_worktree,
        source_repo=source_repo,
        name=name,
        path=path,
        branch=branch,
        base=base,
    )


def _create_worktree(
    *,
    source_repo: Path,
    name: str,
    path: str | None,
    branch: str | None,
    base: str | None,
) -> tuple[WorktreeView, Path]:
    worktree_path, branch_name, base_ref = _worktree_values(
        source_repo=source_repo, name=name, path=path, branch=branch, base=base
    )
    head = _git(["rev-parse", "--short", base_ref], cwd=source_repo)
    _check_branch_available(source_repo, branch_name)
    if worktree_path.exists():
        raise ValidationError(f"worktree path already exists: {worktree_path}")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = _git_ok(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"], source_repo
    )
    args = ["worktree", "add"]
    if branch_exists:
        args += [str(worktree_path), branch_name]
    else:
        args += ["-b", branch_name, str(worktree_path), base_ref]
    _git(args, cwd=source_repo)
    return (
        WorktreeView(
            mode="create",
            state="created",
            path=str(worktree_path),
            branch=branch_name,
            base=base_ref,
            head=head,
            source_repo=str(source_repo),
            created=True,
        ),
        worktree_path,
    )


def _worktree_values(
    *, source_repo: Path, name: str, path: str | None, branch: str | None, base: str | None
) -> tuple[Path, str, str]:
    name_slug = _slug(name)
    repo_slug = _slug(source_repo.name)
    worktree_path = _absolute(Path(path)) if path else worktree_root_path() / repo_slug / name_slug
    branch_name = branch or f"dispatch/{name_slug}"
    base_ref = base or "HEAD"
    return worktree_path, branch_name, base_ref


def _check_branch_available(source_repo: Path, branch: str) -> None:
    for entry in _worktree_list(source_repo):
        if entry.get("branch") == f"refs/heads/{branch}":
            path = entry.get("worktree", "unknown")
            raise ValidationError(f"branch {branch!r} is already checked out in worktree {path}")


def _worktree_list(source_repo: Path) -> list[dict[str, str]]:
    raw = _git(["worktree", "list", "--porcelain"], cwd=source_repo)
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries


def _git_ok(args: list[str], cwd: Path) -> bool:
    return (
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def _git(args: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ValidationError(f"git {' '.join(args)} failed in {cwd}: {detail}")
    return proc.stdout.strip()


def _slug(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-._")
    return slug or "lane"


def _find_environment_file(input_cwd: Path, repo_root: Path | None) -> Path | None:
    roots = [repo_root] if repo_root is not None else [input_cwd, *input_cwd.parents]
    for root in roots:
        candidate = root / _ENV_PATH
        if candidate.is_file():
            return candidate
    return None


def _find_repo_root(cwd: Path) -> Path | None:
    for path in (cwd, *cwd.parents):
        if (path / ".git").exists():
            return path
    return None


def _load_environment(path: Path) -> WorkspaceEnvironmentView:
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"invalid workspace environment {path}: {exc}") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read workspace environment {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"invalid workspace environment {path}: expected table")

    setup = raw.get("setup")
    cleanup = raw.get("cleanup")
    return WorkspaceEnvironmentView(
        version=raw.get("version") if isinstance(raw.get("version"), int) else None,
        name=raw.get("name") if isinstance(raw.get("name"), str) else None,
        setup_script=_script(setup),
        cleanup_script=_script(cleanup),
        unknown_keys=sorted(str(k) for k in raw if k not in _TOP_LEVEL_KEYS),
    )


def _script(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    script = value.get("script")
    return script if isinstance(script, str) else None


async def _run_setup(
    *, script: str, cwd: Path, policy_name: str, timeout_seconds: int
) -> WorkspaceSetupView:
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            script,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise ValidationError(f"failed to start workspace setup {script!r}: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ValidationError(
            f"workspace setup timed out after {timeout_seconds}s: {script}"
        ) from exc

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout_tail = _decode_tail(stdout)
    stderr_tail = _decode_tail(stderr)
    if proc.returncode != 0:
        raise ValidationError(
            f"workspace setup failed with exit {proc.returncode}: {script}"
            + (f"\nstderr: {stderr_tail}" if stderr_tail else "")
        )
    return WorkspaceSetupView(
        policy=policy_name,
        ran=True,
        script=script,
        cwd=str(cwd),
        exit_code=proc.returncode,
        duration_ms=duration_ms,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )


def _decode_tail(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    return text[-_OUTPUT_TAIL_CHARS:] if len(text) > _OUTPUT_TAIL_CHARS else text


def _view(
    *,
    mode: str,
    resolved_mode: str,
    state: WorkspaceState,
    input_cwd: Path,
    effective_cwd: Path,
    setup_view: WorkspaceSetupView,
    repo_root: Path | None = None,
    environment_file: Path | None = None,
    environment: WorkspaceEnvironmentView | None = None,
    worktree_view: WorktreeView | None = None,
) -> WorkspaceView:
    return WorkspaceView(
        mode=mode,
        resolved_mode=resolved_mode,
        state=state,
        input_cwd=str(input_cwd),
        repo_root=str(repo_root) if repo_root is not None else None,
        effective_cwd=str(effective_cwd),
        environment_file=str(environment_file) if environment_file is not None else None,
        environment=environment,
        setup=setup_view,
        worktree=worktree_view or WorktreeView(state="disabled"),
    )


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else (Path.cwd() / expanded).resolve()
