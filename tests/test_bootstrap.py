"""Exercise the contributor bootstrap without touching live application state."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import BinaryIO

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"
SOURCE_FIXTURE_DIRS = ("src", "plugins", "tests", "scripts")
SOURCE_FIXTURE_FILES = ("README.md", "AGENTS.md", "pyproject.toml", "uv.lock", "justfile")


def test_bootstrap_is_directly_executable() -> None:
    assert BOOTSTRAP.is_file()
    assert BOOTSTRAP.stat().st_mode & stat.S_IXUSR


@pytest.fixture
def bootstrap_checkout(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    checkout = tmp_path / "target checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(BOOTSTRAP, scripts / BOOTSTRAP.name)
    (checkout / "pyproject.toml").write_text("[project]\nname = 'bootstrap-probe'\nversion = '0'\n")
    (checkout / "uv.lock").write_text("version = 1\nrevision = 3\nrequires-python = '>=3.13'\n")

    caller = tmp_path / "other checkout"
    caller.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    probe = (
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': dict(os.environ)}))\n"
        "sys.exit(int(os.environ.get('TEST_UV_EXIT', '0')))\n"
    )
    uv = bin_dir / "uv"
    uv.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(probe)} "$@"\n')
    uv.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return checkout, caller, env


def _run_bootstrap(
    checkout: Path,
    caller: Path,
    env: dict[str, str],
    *args: str,
    timeout_seconds: int = 10,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(checkout / "scripts" / "bootstrap.sh"), *args],
        cwd=caller,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _isolated_uv_env(
    tmp_path: Path,
    uv: str,
    *,
    cache: Path,
    python_downloads: str | None = None,
) -> dict[str, str]:
    """Expose uv and this test's compatible Python without caller HOME state."""
    assert sys.version_info >= (3, 13)
    bin_dir = tmp_path / "isolated-tools"
    bin_dir.mkdir(exist_ok=True)
    uv_link = bin_dir / "uv"
    python_link = bin_dir / f"python{sys.version_info.major}.{sys.version_info.minor}"
    if not uv_link.exists():
        uv_link.symlink_to(Path(uv).resolve())
    if not python_link.exists():
        python_link.symlink_to(Path(sys.executable).resolve())
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "UV_CACHE_DIR": str(cache),
        "UV_NO_PROGRESS": "1",
    }
    if python_downloads is not None:
        env["UV_PYTHON_DOWNLOADS"] = python_downloads
    return env


def _copy_shipped_source_fixture(destination: Path) -> None:
    for name in SOURCE_FIXTURE_DIRS:
        shutil.copytree(ROOT / name, destination / name)
    for name in SOURCE_FIXTURE_FILES:
        shutil.copy2(ROOT / name, destination / name)


def _run_git(
    git: str,
    *args: str,
    env: dict[str, str] | None = None,
    stdout: BinaryIO | None = None,
) -> subprocess.CompletedProcess[str]:
    sanitized_env = dict(os.environ if env is None else env)
    for key in tuple(sanitized_env):
        if key.startswith("GIT_"):
            sanitized_env.pop(key)
    sanitized_env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
        }
    )
    return subprocess.run(
        [
            git,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
            "-c",
            "user.name=Bootstrap Test",
            "-c",
            "user.email=bootstrap@example.invalid",
            *args,
        ],
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=True,
    )


def test_git_test_helper_ignores_global_hooks_and_redirecting_selectors(tmp_path: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    home = tmp_path / "hostile-home"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    sentinel = tmp_path / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(sentinel))}\n")
    hook.chmod(0o755)
    (home / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n")
    redirected = tmp_path / "redirected.git"
    env = os.environ | {
        "HOME": str(home),
        "GIT_DIR": str(redirected),
        "GIT_WORK_TREE": str(tmp_path / "redirected-worktree"),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
    }
    repo = tmp_path / "repo"
    repo.mkdir()

    _run_git(git, "init", "-q", str(repo), env=env)
    (repo / "tracked").write_text("fixture")
    _run_git(git, "-C", str(repo), "add", ".", env=env)
    _run_git(git, "-C", str(repo), "commit", "-qm", "fixture", env=env)

    assert not sentinel.exists()
    assert not redirected.exists()


@pytest.mark.parametrize("config_encoding", ["count", "parameters"])
def test_git_test_helper_ignores_environment_configured_clean_filters(
    tmp_path: Path,
    config_encoding: str,
) -> None:
    git = shutil.which("git")
    assert git is not None
    attributes = tmp_path / "private-attributes"
    attributes.write_text("fixture filter=reviewprobe\n")
    sentinel = tmp_path / "clean-filter-ran"
    config = {
        "core.attributesfile": str(attributes),
        "filter.reviewprobe.clean": f"touch {shlex.quote(str(sentinel))}",
    }
    env = dict(os.environ)
    if config_encoding == "count":
        env["GIT_CONFIG_COUNT"] = str(len(config))
        for index, (key, value) in enumerate(config.items()):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
    else:
        env["GIT_CONFIG_PARAMETERS"] = " ".join(f"'{key}={value}'" for key, value in config.items())
    repo = tmp_path / "repo"
    repo.mkdir()

    _run_git(git, "init", "-q", str(repo), env=env)
    (repo / "fixture").write_text("fixture")
    _run_git(git, "-C", str(repo), "add", "fixture", env=env)

    assert not sentinel.exists()


def test_bootstrap_syncs_its_own_checkout_from_another_cwd(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout

    result = _run_bootstrap(checkout, caller, env)

    assert result.returncode == 0, result.stderr
    invocation = json.loads(result.stdout)
    checkout = checkout.resolve()
    assert Path(invocation["cwd"]) == checkout
    assert invocation["argv"] == [
        "sync",
        "--locked",
        "--group",
        "dev",
        "--directory",
        str(checkout),
        "--project",
        str(checkout),
    ]
    assert not list(caller.iterdir())


def test_bootstrap_normalizes_ambient_checkout_and_install_selectors(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout
    cleared = (
        "CDPATH",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "UV_WORKING_DIR",
        "UV_WORKING_DIRECTORY",
        "UV_PROJECT",
        "UV_PYTHON",
        "UV_FROZEN",
        "UV_NO_DEV",
        "UV_NO_GROUP",
        "UV_NO_EDITABLE",
        "UV_NO_INSTALL_PROJECT",
        "UV_NO_INSTALL_WORKSPACE",
        "UV_NO_INSTALL_LOCAL",
        "UV_NO_INSTALL_PACKAGE",
        "PYTHONHOME",
        "PYTHONPATH",
    )
    env.update(dict.fromkeys(cleared, str(caller)))
    env["UV_INDEX_URL"] = "https://packages.example.invalid/simple"

    result = _run_bootstrap(checkout, caller, env)

    assert result.returncode == 0, result.stderr
    child_env = json.loads(result.stdout)["env"]
    assert child_env["UV_PROJECT_ENVIRONMENT"] == str(checkout.resolve() / ".venv")
    assert all(key not in child_env for key in cleared if key != "UV_PROJECT_ENVIRONMENT")
    assert child_env["UV_INDEX_URL"] == env["UV_INDEX_URL"]


@pytest.mark.parametrize("required_file", ["pyproject.toml", "uv.lock"])
def test_bootstrap_requires_a_locked_uv_project_before_sync(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
    required_file: str,
) -> None:
    checkout, caller, env = bootstrap_checkout
    (checkout / required_file).unlink()

    result = _run_bootstrap(checkout, caller, env)

    assert result.returncode == 1
    assert required_file in result.stderr
    assert not result.stdout
    assert not (checkout / ".venv").exists()


def test_bootstrap_explains_missing_uv_without_installing_it(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout
    env["PATH"] = ""

    result = _run_bootstrap(checkout, caller, env)

    assert result.returncode == 127
    assert "uv is required on PATH" in result.stderr
    assert not result.stdout
    assert not (checkout / ".venv").exists()


@pytest.mark.parametrize("existing_target", [True, False])
def test_bootstrap_refuses_a_live_or_dangling_venv_symlink(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
    existing_target: bool,
) -> None:
    checkout, caller, env = bootstrap_checkout
    other_venv = caller / ".venv"
    if existing_target:
        other_venv.mkdir()
        (other_venv / "keep").write_text("not ours")
    (checkout / ".venv").symlink_to(other_venv, target_is_directory=True)

    result = _run_bootstrap(checkout, caller, env)

    assert result.returncode == 1
    assert "symlink" in result.stderr
    assert not result.stdout
    assert (checkout / ".venv").is_symlink()
    if existing_target:
        assert (other_venv / "keep").read_text() == "not ours"


def test_bootstrap_rejects_arguments_before_sync(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout

    result = _run_bootstrap(checkout, caller, env, "codex")

    assert result.returncode == 2
    assert "does not accept arguments" in result.stderr
    assert not result.stdout


def test_bootstrap_rejects_a_relocated_script_symlink(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout
    relocated = caller / "bootstrap.sh"
    relocated.symlink_to(checkout / "scripts" / "bootstrap.sh")

    result = subprocess.run(
        [str(relocated)],
        cwd=caller,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 1
    assert "script symlink" in result.stderr
    assert not result.stdout


def test_bootstrap_propagates_the_exact_uv_failure(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout
    env["TEST_UV_EXIT"] = "23"

    assert _run_bootstrap(checkout, caller, env).returncode == 23


def test_bootstrap_is_repeatable(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout

    first = _run_bootstrap(checkout, caller, env)
    second = _run_bootstrap(checkout, caller, env)

    assert first.returncode == second.returncode == 0
    assert json.loads(first.stdout)["argv"] == json.loads(second.stdout)["argv"]


def test_bootstrap_resolves_a_symlinked_ancestor_to_the_physical_checkout(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout
    checkout_alias = checkout.parent / "checkout alias"
    checkout_alias.symlink_to(checkout, target_is_directory=True)

    result = _run_bootstrap(checkout_alias, caller, env)

    assert result.returncode == 0, result.stderr
    invocation = json.loads(result.stdout)
    assert Path(invocation["cwd"]) == checkout.resolve()
    assert invocation["env"]["UV_PROJECT_ENVIRONMENT"] == str(checkout.resolve() / ".venv")


def test_bootstrap_leaves_git_graphite_and_live_application_state_unchanged(
    bootstrap_checkout: tuple[Path, Path, dict[str, str]],
) -> None:
    checkout, caller, env = bootstrap_checkout
    sentinels = [
        caller / ".git" / "keep",
        Path(env["HOME"]) / ".codex" / "keep",
        Path(env["HOME"]) / ".dispatch" / "keep",
        Path(env["HOME"]) / ".config" / "graphite" / "keep",
    ]
    for sentinel in sentinels:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("unchanged")

    result = _run_bootstrap(checkout, caller, env)

    assert result.returncode == 0, result.stderr
    assert all(sentinel.read_text() == "unchanged" for sentinel in sentinels)


def test_just_setup_is_a_thin_bootstrap_alias() -> None:
    justfile = (ROOT / "justfile").read_text()

    assert "setup:\n    ./scripts/bootstrap.sh\n" in justfile


def test_bootstrap_uses_real_uv_in_a_source_export_with_hostile_selectors(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    checkout = tmp_path / "real source export"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(BOOTSTRAP, scripts / BOOTSTRAP.name)
    (checkout / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'bootstrap-probe'\n"
        "version = '0'\n"
        "requires-python = '>=3.13'\n"
        "dependencies = []\n"
        "[dependency-groups]\n"
        "dev = []\n"
        "[tool.uv]\n"
        "package = false\n"
    )
    caller = tmp_path / "unrelated cwd"
    caller.mkdir()
    hostile_working_dir = caller / "not-a-directory"
    hostile_working_dir.write_text("sentinel")
    env = _isolated_uv_env(
        tmp_path,
        uv,
        cache=tmp_path / "uv-cache",
        python_downloads="never",
    )
    lock = subprocess.run(
        [uv, "lock", "--directory", str(checkout)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert lock.returncode == 0, lock.stderr
    lock_before = (checkout / "uv.lock").read_bytes()
    env.update(
        {
            "UV_WORKING_DIR": str(hostile_working_dir),
            "UV_WORKING_DIRECTORY": str(caller),
            "UV_PROJECT_ENVIRONMENT": str(tmp_path / "foreign-venv"),
            "UV_NO_DEV": "1",
            "UV_NO_GROUP": "dev",
            "UV_NO_EDITABLE": "1",
            "UV_NO_INSTALL_PROJECT": "1",
            "UV_FROZEN": "1",
        }
    )

    result = _run_bootstrap(checkout, caller, env, timeout_seconds=300)

    assert result.returncode == 0, result.stderr
    assert (checkout / "uv.lock").read_bytes() == lock_before
    assert (checkout / ".venv").is_dir()
    assert not (tmp_path / "foreign-venv").exists()
    prefix = subprocess.run(
        [str(checkout / ".venv" / "bin" / "python"), "-c", "import sys; print(sys.prefix)"],
        env={"PATH": env["PATH"]},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    assert Path(os.path.realpath(prefix)) == Path(os.path.realpath(checkout / ".venv"))


@pytest.mark.parametrize("config_encoding", ["count", "parameters"])
def test_real_uv_bootstraps_the_project_in_clone_worktree_and_source_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_encoding: str,
) -> None:
    uv = shutil.which("uv")
    git = shutil.which("git")
    assert uv is not None
    assert git is not None
    if config_encoding == "count":
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "never")
    else:
        monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'protocol.file.allow=never'")
    seed = tmp_path / "seed"
    seed.mkdir()
    _copy_shipped_source_fixture(seed)
    _run_git(git, "init", "-q", str(seed))
    _run_git(git, "-C", str(seed), "add", ".")
    _run_git(git, "-C", str(seed), "commit", "-qm", "fixture")

    clone = tmp_path / "ordinary clone"
    _run_git(git, "clone", "-q", "--no-local", str(seed), str(clone))
    worktree = tmp_path / "detached worktree"
    _run_git(git, "-C", str(seed), "worktree", "add", "-q", "--detach", str(worktree))
    archive = tmp_path / "source.tar"
    with archive.open("wb") as output:
        _run_git(
            git,
            "-C",
            str(seed),
            "archive",
            "--format=tar",
            "--prefix=source-export/",
            "HEAD",
            stdout=output,
        )
    with tarfile.open(archive) as tf:
        tf.extractall(tmp_path, filter="data")
    source_export = tmp_path / "source-export"
    assert not (source_export / ".git").exists()

    caller = tmp_path / "caller"
    caller.mkdir()
    cache = tmp_path / "uv-cache"
    for checkout in (clone, worktree, source_export):
        lock_before = (checkout / "uv.lock").read_bytes()
        env = _isolated_uv_env(tmp_path, uv, cache=cache)
        result = _run_bootstrap(checkout, caller, env, timeout_seconds=300)
        assert result.returncode == 0, result.stderr
        assert (checkout / "uv.lock").read_bytes() == lock_before
        python = checkout / ".venv" / "bin" / "python"
        probe = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import json, pathlib, sys, pytest, outfitter.dispatch.cli as cli; "
                    "print(json.dumps({'prefix': sys.prefix, 'module': cli.__file__}))"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        facts = json.loads(probe.stdout)
        assert Path(facts["prefix"]).resolve() == (checkout / ".venv").resolve()
        assert (
            Path(facts["module"]).resolve()
            == (checkout / "src" / "outfitter" / "dispatch" / "cli.py").resolve()
        )
        help_result = subprocess.run(
            [str(checkout / ".venv" / "bin" / "dispatch"), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert help_result.returncode == 0, help_result.stderr
        assert "Usage:" in help_result.stdout


def test_real_uv_bootstraps_a_freshly_built_sdist(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    env = _isolated_uv_env(tmp_path, uv, cache=tmp_path / "uv-cache")
    dist = tmp_path / "dist"
    build = subprocess.run(
        [uv, "build", "--sdist", "--out-dir", str(dist)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    archives = list(dist.glob("*.tar.gz"))
    assert len(archives) == 1
    extracted_parent = tmp_path / "extracted"
    extracted_parent.mkdir()
    with tarfile.open(archives[0]) as tf:
        tf.extractall(extracted_parent, filter="data")
    extracted = next(path for path in extracted_parent.iterdir() if path.is_dir())
    caller = tmp_path / "caller"
    caller.mkdir()

    result = subprocess.run(
        [str(extracted / "scripts" / "bootstrap.sh")],
        cwd=caller,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    python = extracted / ".venv" / "bin" / "python"
    probe = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import json, sys, outfitter.dispatch.cli as cli; "
                "print(json.dumps({'prefix': sys.prefix, 'module': cli.__file__}))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    facts = json.loads(probe.stdout)
    assert Path(facts["prefix"]).resolve() == (extracted / ".venv").resolve()
    assert (
        Path(facts["module"]).resolve()
        == (extracted / "src" / "outfitter" / "dispatch" / "cli.py").resolve()
    )
    help_result = subprocess.run(
        [str(extracted / ".venv" / "bin" / "dispatch"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "Usage:" in help_result.stdout


def test_real_uv_rejects_a_stale_lock_without_rewriting_it(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    checkout = tmp_path / "stale lock checkout"
    checkout.mkdir()
    _copy_shipped_source_fixture(checkout)
    pyproject = checkout / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            '    "typer>=0.15",',
            '    "typer>=0.15",\n    "sniffio==1.3.0",',
        )
    )
    lock_before = (checkout / "uv.lock").read_bytes()
    caller = tmp_path / "caller"
    caller.mkdir()
    env = _isolated_uv_env(tmp_path, uv, cache=tmp_path / "uv-cache")
    env["UV_FROZEN"] = "1"

    result = _run_bootstrap(checkout, caller, env, timeout_seconds=300)

    assert result.returncode != 0
    assert "lock" in result.stderr.lower()
    assert (checkout / "uv.lock").read_bytes() == lock_before


def test_ci_uses_the_same_bootstrap_entrypoint() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow_path.is_file():
        pytest.skip("CI configuration is a Git-checkout-only asset")
    workflow = workflow_path.read_text()

    assert "run: ./scripts/bootstrap.sh" in workflow
    assert "run: uv sync" not in workflow


def test_ci_bootstraps_the_exact_head_on_macos() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow_path.is_file():
        pytest.skip("CI configuration is a Git-checkout-only asset")
    workflow = workflow_path.read_text()

    assert "runs-on: macos-latest" in workflow
    assert workflow.count("run: ./scripts/bootstrap.sh") >= 2


def test_release_waits_for_linux_checks_and_macos_bootstrap() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow_path.is_file():
        pytest.skip("CI configuration is a Git-checkout-only asset")
    workflow = workflow_path.read_text()

    release = workflow.split("  release:\n", maxsplit=1)[1]
    assert "needs: [check, bootstrap-macos]" in release


def test_contributor_docs_describe_the_neutral_bootstrap_contract() -> None:
    agents = (ROOT / "AGENTS.md").read_text()
    readme = (ROOT / "README.md").read_text()
    docs = agents + readme

    assert "./scripts/bootstrap.sh" in agents
    assert "./scripts/bootstrap.sh" in readme
    assert "macOS and Linux" in docs
    assert "UV_WORKING_DIR" in agents
    assert "UV_WORKING_DIRECTORY" in agents
    assert "UV_FROZEN" in agents
    assert "hermes-setup.sh" not in docs
    assert ".hermes/skills/dispatch-worktree" not in docs


def test_contributor_docs_use_one_setup_recipe_and_qualify_checkout_only_reading() -> None:
    agents = (ROOT / "AGENTS.md").read_text()
    usage_path = ROOT / "docs" / "usage" / "README.md"
    if not usage_path.is_file():
        pytest.skip("operator docs are Git-checkout-only assets")
    usage = usage_path.read_text()

    assert "`uv sync` to install" not in agents
    assert "\nuv sync\n" not in usage
    contributor_usage = usage.split("For development from this repo", maxsplit=1)[1].split(
        "Start the singleton daemon", maxsplit=1
    )[0]
    assert "AGENTS.md" in contributor_usage
    assert "./scripts/bootstrap.sh" not in contributor_usage
    assert "\nuv run " not in contributor_usage
    assert "full Git checkout" in agents
    assert "Packaged source exports rely on `pyproject.toml`" in agents


def test_usage_docs_do_not_imply_bootstrap_arguments_or_teardown() -> None:
    usage_path = ROOT / "docs" / "usage" / "README.md"
    if not usage_path.is_file():
        pytest.skip("operator docs are Git-checkout-only assets")
    usage = usage_path.read_text()

    assert "bootstrap.sh codex" not in usage
    assert "bootstrap.sh teardown" not in usage
