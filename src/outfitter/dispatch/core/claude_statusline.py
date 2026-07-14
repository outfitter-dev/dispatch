"""Privacy-bounded Claude statusline capacity capture."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, ValidationError, field_validator

from outfitter.dispatch.config import claude_statusline_snapshot_path
from outfitter.dispatch.registry.models import ProviderCapacityWindow

_MAX_INPUT_BYTES = 1024 * 1024
_MAX_SNAPSHOT_BYTES = 64 * 1024
_VERSION_PATTERN = re.compile(r"\b\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b")


class StatuslineCaptureError(Exception):
    """Raised when statusline input cannot be safely normalized."""


class ClaudeStatuslineWindow(BaseModel):
    used_percentage: float = Field(ge=0, le=100)
    resets_at: int | None = Field(default=None, ge=0)


class ClaudeStatuslineRateLimits(BaseModel):
    five_hour: ClaudeStatuslineWindow | None = None
    seven_day: ClaudeStatuslineWindow | None = None


class ClaudeStatuslineSnapshot(BaseModel):
    schema_version: Literal[1] = 1
    observed_at: str
    claude_code_version: str | None = Field(default=None, max_length=40)
    session_fingerprint: str | None = Field(default=None, max_length=40)
    model_label: str | None = Field(default=None, max_length=120)
    rate_limits: ClaudeStatuslineRateLimits = Field(default_factory=ClaudeStatuslineRateLimits)

    @field_validator("observed_at")
    @classmethod
    def _valid_observed_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("observed_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return value


def _fingerprint(value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"sha256:{digest[:24]}"


def _bounded(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    if not collapsed:
        return None
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def _version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _VERSION_PATTERN.search(value)
    return match.group(0) if match is not None else None


def _window(value: object) -> ClaudeStatuslineWindow | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StatuslineCaptureError("incompatible statusline window")
    raw = cast(dict[str, object], value)
    used = raw.get("used_percentage")
    resets_at = raw.get("resets_at")
    if isinstance(used, bool) or not isinstance(used, int | float):
        raise StatuslineCaptureError("incompatible statusline window")
    if resets_at is not None and (isinstance(resets_at, bool) or not isinstance(resets_at, int)):
        raise StatuslineCaptureError("incompatible statusline window")
    return ClaudeStatuslineWindow(used_percentage=float(used), resets_at=resets_at)


def _normalize(payload: bytes, *, observed_at: str) -> ClaudeStatuslineSnapshot:
    if len(payload) > _MAX_INPUT_BYTES:
        raise StatuslineCaptureError("statusline input too large")
    try:
        raw_payload = json.loads(payload)
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
        raise StatuslineCaptureError("invalid statusline JSON") from exc
    if not isinstance(raw_payload, dict):
        raise StatuslineCaptureError("incompatible statusline JSON")
    raw = cast(dict[str, object], raw_payload)
    raw_limits = raw.get("rate_limits")
    if raw_limits is not None and not isinstance(raw_limits, dict):
        raise StatuslineCaptureError("incompatible statusline rate limits")
    limits = cast(dict[str, object], raw_limits) if isinstance(raw_limits, dict) else {}
    try:
        return ClaudeStatuslineSnapshot(
            observed_at=observed_at,
            claude_code_version=_version(raw.get("version")),
            session_fingerprint=_fingerprint(cast(str, raw["session_id"]))
            if isinstance(raw.get("session_id"), str)
            else None,
            model_label=_bounded(
                cast(dict[str, object], raw.get("model", {})).get("display_name")
                if isinstance(raw.get("model"), dict)
                else None,
                120,
            ),
            rate_limits=ClaudeStatuslineRateLimits(
                five_hour=_window(limits.get("five_hour")),
                seven_day=_window(limits.get("seven_day")),
            ),
        )
    except ValidationError as exc:
        raise StatuslineCaptureError("incompatible statusline values") from exc


def capture_claude_statusline(
    payload: bytes,
    *,
    path: Path | None = None,
    observed_at: str | None = None,
) -> ClaudeStatuslineSnapshot:
    """Normalize stdin JSON and atomically replace the local snapshot."""

    snapshot = _normalize(
        payload,
        observed_at=observed_at or datetime.now(UTC).isoformat(),
    )
    destination = path or claude_statusline_snapshot_path()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = snapshot.model_dump_json().encode()
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return snapshot


def read_claude_statusline_snapshot(*, path: Path | None = None) -> ClaudeStatuslineSnapshot | None:
    """Read a bounded normalized snapshot, returning no raw parse detail."""

    source = path or claude_statusline_snapshot_path()
    try:
        with source.open("rb") as handle:
            payload = handle.read(_MAX_SNAPSHOT_BYTES + 1)
        if len(payload) > _MAX_SNAPSHOT_BYTES:
            return None
        return ClaudeStatuslineSnapshot.model_validate_json(payload)
    except (FileNotFoundError, OSError, ValidationError):
        return None


def statusline_capacity_windows(
    snapshot: ClaudeStatuslineSnapshot,
) -> list[ProviderCapacityWindow]:
    """Project a normalized snapshot onto provider-neutral windows."""

    windows: list[ProviderCapacityWindow] = []
    for name, duration, window in (
        ("five_hour", 5 * 60, snapshot.rate_limits.five_hour),
        ("seven_day", 7 * 24 * 60, snapshot.rate_limits.seven_day),
    ):
        if window is None:
            continue
        windows.append(
            ProviderCapacityWindow(
                limit_id="claude.ai",
                limit_name="Claude.ai subscriber",
                window=name,
                used_percent=window.used_percentage,
                remaining_percent=100 - window.used_percentage,
                duration_minutes=duration,
                resets_at=window.resets_at,
                observed_at=snapshot.observed_at,
            )
        )
    return windows
