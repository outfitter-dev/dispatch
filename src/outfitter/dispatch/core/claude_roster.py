"""Claude Agent View roster parsing and launch identity reconciliation."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from outfitter.dispatch.core.claude_launch_types import (
    ClaudeLaunchAmbiguousError,
    ClaudeLaunchObservation,
    ClaudeLaunchOutputError,
)


def _optional_text(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    return value if isinstance(value, str) and value else None


def reconcile_claude_launch(
    *, short_id: str, launch_cwd: Path, roster_output: str
) -> ClaudeLaunchObservation:
    """Resolve one provisional short ID to exactly one authoritative provider UUID."""

    try:
        raw = json.loads(roster_output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClaudeLaunchOutputError("Claude agent roster returned invalid JSON") from exc
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise ClaudeLaunchOutputError("Claude agent roster returned incompatible JSON")
    rows = [
        cast(dict[str, object], row)
        for row in raw
        if isinstance(row.get("id"), str) and str(row["id"]).lower() == short_id.lower()
    ]
    if not rows:
        return ClaudeLaunchObservation(
            provider="claude",
            reconciliation="pending",
            short_id=short_id,
            provider_session_id=None,
            launch_cwd=str(launch_cwd),
        )
    if len(rows) > 1:
        raise ClaudeLaunchAmbiguousError("Claude launch matched multiple global roster rows")
    row = rows[0]
    kind = row.get("kind")
    if kind is not None and kind != "background":
        raise ClaudeLaunchOutputError("Claude roster row is not a background session")
    session_id = row.get("sessionId")
    if session_id is None:
        return ClaudeLaunchObservation(
            provider="claude",
            reconciliation="pending",
            short_id=short_id,
            provider_session_id=None,
            launch_cwd=str(launch_cwd),
            observed_cwd=_optional_text(row, "cwd"),
            observed_name=_optional_text(row, "name"),
            observed_kind=_optional_text(row, "kind"),
            observed_state=_optional_text(row, "state"),
            observed_worktree=_optional_text(row, "worktree"),
        )
    if not isinstance(session_id, str):
        raise ClaudeLaunchOutputError("Claude roster session identity has an incompatible type")
    try:
        provider_session_id = str(uuid.UUID(session_id))
    except ValueError as exc:
        raise ClaudeLaunchOutputError("Claude roster session identity is not a full UUID") from exc
    return ClaudeLaunchObservation(
        provider="claude",
        reconciliation="reconciled",
        short_id=short_id,
        provider_session_id=provider_session_id,
        launch_cwd=str(launch_cwd),
        observed_cwd=_optional_text(row, "cwd"),
        observed_name=_optional_text(row, "name"),
        observed_kind=_optional_text(row, "kind"),
        observed_state=_optional_text(row, "state"),
        observed_worktree=_optional_text(row, "worktree"),
    )
