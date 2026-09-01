"""Managed usage-capture files: wrapper script and restoration record.

Shared by ``dispatch usage-capture run`` (the high-frequency delegation path)
and the install/status/remove lifecycle built on top of it. Canonical location
is ``~/.dispatch/claude/`` (``DISPATCH_HOME``-relative so tests stay isolated).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, field_validator
from pydantic import ValidationError as PydanticValidationError

from outfitter.dispatch import config
from outfitter.dispatch.contracts.errors import ValidationError

WRAPPER_FILENAME = "statusline.sh"
RECORD_FILENAME = "statusline.original.json"

_MAX_RECORD_BYTES = 64 * 1024


class UsageCaptureRecord(BaseModel):
    """Restoration record persisted as ``statusline.original.json``.

    ``original_statusline`` holds the user's pre-install ``statusLine`` settings
    object exactly as found: known keys (``command``, ``padding``,
    ``refreshInterval``, ``hideVimModeIndicator``) and unknown keys alike are
    preserved verbatim so remove can restore precisely what install replaced.
    """

    schema_version: Literal[1] = 1
    provider: Literal["claude"]
    had_statusline: bool
    original_statusline: dict[str, JsonValue] | None = None
    installed_command: str = Field(min_length=1)
    installed_at: str

    @field_validator("installed_at")
    @classmethod
    def _valid_installed_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("installed_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("installed_at must include a timezone")
        return value

    def original_command(self) -> str | None:
        """The original renderer command string, when one existed."""
        if not self.had_statusline or self.original_statusline is None:
            return None
        command = self.original_statusline.get("command")
        if isinstance(command, str) and command.strip():
            return command
        return None


def usage_capture_wrapper_path(directory: Path | None = None) -> Path:
    """Canonical path of the installed statusline wrapper script."""
    return (directory or config.claude_usage_capture_dir()) / WRAPPER_FILENAME


def usage_capture_record_path(directory: Path | None = None) -> Path:
    """Canonical path of the restoration record."""
    return (directory or config.claude_usage_capture_dir()) / RECORD_FILENAME


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (with parents) and keep it owner-only (0o700)."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def write_private_file(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Atomically replace ``path`` with owner-only permissions.

    ``mode`` defaults to 0o600; the executable wrapper passes 0o700. The write
    goes through a same-directory temp file + ``os.replace`` so concurrent
    readers never observe a partial file.
    """
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def record_too_large(record: UsageCaptureRecord) -> bool:
    """Whether the serialized record would exceed the read-side size cap.

    Uses the exact serialization ``write_usage_capture_record`` persists (JSON,
    indent 2, trailing newline), so a record that passes here is guaranteed to
    read back through ``read_usage_capture_record`` rather than being rejected
    as oversize.
    """
    return len(record.model_dump_json(indent=2).encode()) + 1 > _MAX_RECORD_BYTES


def write_usage_capture_record(record: UsageCaptureRecord, *, path: Path | None = None) -> Path:
    """Persist the restoration record atomically under an owner-only directory.

    Raises :class:`ValidationError` (dispatch's, not pydantic's) before touching
    the filesystem when the record serializes beyond ``read_usage_capture_record``'s
    size cap — writing it would persist a record that can never be read back.
    """
    payload = record.model_dump_json(indent=2).encode() + b"\n"
    if len(payload) > _MAX_RECORD_BYTES:
        raise ValidationError(
            f"restoration record serializes to {len(payload)} bytes, over the "
            f"{_MAX_RECORD_BYTES}-byte limit read_usage_capture_record enforces"
        )
    destination = path or usage_capture_record_path()
    ensure_private_dir(destination.parent)
    write_private_file(destination, payload)
    return destination


def read_usage_capture_record(*, path: Path | None = None) -> UsageCaptureRecord | None:
    """Read the restoration record; missing, oversize, or corrupt reads as ``None``.

    Callers on the statusline hot path treat ``None`` as fail-visually-open.
    """
    source = path or usage_capture_record_path()
    try:
        with source.open("rb") as handle:
            payload = handle.read(_MAX_RECORD_BYTES + 1)
        if len(payload) > _MAX_RECORD_BYTES:
            return None
        return UsageCaptureRecord.model_validate_json(payload)
    except (OSError, PydanticValidationError):
        return None
