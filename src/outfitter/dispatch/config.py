"""Runtime paths and local daemon policy.

Overridable via env so tests never touch real user state.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
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


def pidfile_path() -> Path:
    """Pidfile for the singleton daemon (ADR-0009)."""
    override = os.environ.get("DISPATCH_PIDFILE")
    return Path(override) if override else _base() / "dispatchd.pid"


def config_path() -> Path:
    """Local dispatch daemon config."""
    override = os.environ.get("DISPATCH_CONFIG")
    return Path(override) if override else _base() / "config.toml"


def ensure_base() -> Path:
    base = _base()
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass(frozen=True)
class RuntimePolicy:
    """Local operator policy for authority gates.

    Attached writes are intentionally off by default: enabling them means this
    daemon may start turns or inject context into desktop-created threads even
    though the desktop app cannot be gated by Dispatch's advisory lock.
    """

    allow_attached_writes: bool = False


def runtime_policy() -> RuntimePolicy:
    """Read local policy from env and ``~/.dispatch/config.toml``.

    Env wins so test and one-shot operator shells can force policy without
    mutating the user's config file.
    """
    value = os.environ.get("DISPATCH_ALLOW_ATTACHED_WRITES")
    if value is not None:
        return RuntimePolicy(allow_attached_writes=_truthy(value))

    path = config_path()
    if not path.exists():
        return RuntimePolicy()

    with path.open("rb") as f:
        raw = tomllib.load(f)
    policy = raw.get("policy", raw)
    if not isinstance(policy, dict):
        return RuntimePolicy()
    return RuntimePolicy(allow_attached_writes=bool(policy.get("allow_attached_writes", False)))


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
