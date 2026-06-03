"""Shared test fixtures.

AF_UNIX socket paths are capped at ``sun_path`` (104 bytes on macOS/BSD, 108 on
Linux). pytest's ``tmp_path`` lives under ``$TMPDIR``, which on macOS defaults to
a long ``/var/folders/.../T/...`` path; binding a control socket there overflows
the cap and fails with ``OSError: AF_UNIX path too long``. Production sockets live
under ``~/.dispatch`` (short), so this is purely a test-path concern. ``socket_dir``
allocates sockets under a guaranteed-short root, so the suite is portable without
callers exporting ``TMPDIR=/tmp``.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Smaller of the two platform sun_path limits; staying under it is portable.
SUN_PATH_MAX = 104


def _short_tmp_root() -> Path:
    """Shortest writable temp root, independent of a long ``$TMPDIR``."""
    candidate = Path("/tmp")  # short root for AF_UNIX sockets, not user data
    if candidate.is_dir():
        return candidate
    return Path(tempfile.gettempdir())


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A short-path directory for binding AF_UNIX sockets in tests.

    Use ``socket_dir / "name.sock"`` instead of ``tmp_path / "name.sock"`` anywhere
    a test binds or connects to a Unix socket; keep other artifacts under ``tmp_path``.
    """
    path = Path(tempfile.mkdtemp(prefix="dx-", dir=_short_tmp_root()))
    sample = path / "dispatchd.sock"
    assert len(str(sample)) <= SUN_PATH_MAX, f"socket root too long: {sample}"
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
