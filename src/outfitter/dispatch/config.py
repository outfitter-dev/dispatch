"""Runtime paths. Overridable via env so tests never touch real user state."""

from __future__ import annotations

import os
from pathlib import Path


def _base() -> Path:
    return Path(os.environ.get("DISPATCH_HOME", "~/.dispatch")).expanduser()


def socket_path() -> Path:
    """Unix control socket the daemon listens on and surfaces connect to."""
    override = os.environ.get("DISPATCH_SOCKET")
    return Path(override) if override else _base() / "dispatchd.sock"


def db_path() -> Path:
    """SQLite registry path."""
    override = os.environ.get("DISPATCH_DB")
    return Path(override) if override else _base() / "registry.db"


def ensure_base() -> Path:
    base = _base()
    base.mkdir(parents=True, exist_ok=True)
    return base
