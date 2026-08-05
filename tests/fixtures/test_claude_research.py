"""Keep Claude research capability blockers executable in the repository gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run_probe(mode: str, fixture: str) -> None:
    root = Path(__file__).parents[2]
    subprocess.run(
        [
            sys.executable,
            str(root / "spikes/claude/assert_probe.py"),
            mode,
            str(root / f"spikes/claude/fixtures/{fixture}"),
        ],
        check=True,
        cwd=root,
    )


def test_human_coexistence_capability_policy_remains_blocked() -> None:
    root = Path(__file__).parents[2]
    policy = json.loads((root / "spikes/claude/fixtures/capability-policy.json").read_text())[
        "human_coexistence"
    ]
    assert policy == {
        "status": "blocked",
        "supported": False,
        "available_now": False,
        "reason": "transport_blocked",
        "negative_evidence": "coexistence-outcomes.jsonl",
        "candidate": "agent-view-cockpit",
        "candidate_plan": "agent-view-cockpit-plan.jsonl",
        "positive_gate": "pinned-live-one-shared-history",
    }
    run_probe("coexistence-fixture", "coexistence-outcomes.jsonl")


def test_agent_view_cockpit_plan_retains_receipt_blocker() -> None:
    run_probe("cockpit-plan-fixture", "agent-view-cockpit-plan.jsonl")


def test_persistent_owner_message_completes_without_process_exit() -> None:
    run_probe("message-receipt", "persistent-owner-completion.jsonl")


def test_preflight_sanitizer_requires_current_nonce() -> None:
    root = Path(__file__).parents[2]
    env = os.environ.copy()
    env["DISPATCH_CLAUDE_PREFLIGHT_NONCE"] = "preflight-current"
    result = subprocess.run(
        [
            "jq",
            "-cf",
            str(root / "spikes/claude/sanitize_stream.jq"),
            str(root / "spikes/claude/fixtures/preflight-nonce-raw.jsonl"),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
    )
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert "dispatch_preflight" not in events[0]
    assert events[1]["dispatch_preflight"] is True
    assert all("stdout" not in event for event in events)
