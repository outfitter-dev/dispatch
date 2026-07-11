"""Fast checks for live scenario fixture definitions."""

from __future__ import annotations

import subprocess
import sys


def test_basic_scenario_dry_run_validates() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_scenario.py",
            "--dry-run",
            "tests/scenarios/basic_coordination.toml",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "scenario=basic_coordination" in result.stdout
    assert "dispatch_bin=uv run --project" in result.stdout
    assert "lane alpha" in result.stdout
    assert "lane beta" in result.stdout


def test_interactive_request_scenario_dry_run_validates() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_scenario.py",
            "--dry-run",
            "tests/scenarios/interactive_requests.toml",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "scenario=interactive_requests" in result.stdout
    assert "owned_interactive_requests=permissive" in result.stdout
    assert "lane approval" in result.stdout


def test_canonical_item_ingestion_scenario_dry_run_validates() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_scenario.py",
            "--dry-run",
            "tests/scenarios/canonical_item_ingestion.toml",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "scenario=canonical_item_ingestion" in result.stdout
    assert "lane canonical" in result.stdout


def test_bounded_history_sync_scenario_dry_run_validates() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_scenario.py",
            "--dry-run",
            "tests/scenarios/bounded_history_sync.toml",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "scenario=bounded_history_sync" in result.stdout
    assert "verify_bounded_sync=True" in result.stdout
    assert "unmanaged_sync=True" in result.stdout
    assert "lane bounded" in result.stdout
