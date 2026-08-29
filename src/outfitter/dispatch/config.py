"""Runtime paths and local daemon policy.

Overridable via env so tests never touch real user state.
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

CaptureMode = Literal["minimal", "standard", "debug"]
RawPayloadRetention = Literal["off", "errors", "debug", "all"]
InteractiveRequestMode = Literal["deny", "attention", "permissive"]


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


def worktree_root_path() -> Path:
    """Default root for Dispatch-created git worktrees."""
    override = os.environ.get("DISPATCH_WORKTREE_ROOT")
    return Path(override).expanduser() if override else _base() / "worktrees"


def config_path() -> Path:
    """Local dispatch daemon config."""
    override = os.environ.get("DISPATCH_CONFIG")
    return Path(override) if override else _base() / "config.toml"


def app_server_socket_path() -> Path | None:
    """Shared App Server socket, or ``None`` when Dispatch should own stdio.

    The environment override supports bounded one-shot experiments. The local
    config form makes the same explicit endpoint durable without changing the
    default process-ownership contract.
    """
    override = os.environ.get("DISPATCH_APP_SERVER_SOCKET")
    if override is not None:
        return _absolute_socket_path(override, "DISPATCH_APP_SERVER_SOCKET")

    path = config_path()
    if not path.exists():
        return None
    with path.open("rb") as f:
        raw = tomllib.load(f)
    raw_app_server = raw.get("app_server", {})
    if not isinstance(raw_app_server, dict):
        raise ValueError("app_server must be a TOML table")
    value = raw_app_server.get("socket_path")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("app_server.socket_path must be a string")
    return _absolute_socket_path(value, "app_server.socket_path")


def _absolute_socket_path(value: str, name: str) -> Path:
    path = Path(value).expanduser()
    if not value.strip() or not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


def claude_statusline_snapshot_path() -> Path:
    """Normalized Claude statusline capacity snapshot."""

    return _base() / "providers" / "claude" / "statusline.json"


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
    allow_workspace_setup: bool = False
    workspace_setup_timeout_seconds: int = 120
    owned_interactive_requests: InteractiveRequestMode = "attention"
    attached_interactive_requests: InteractiveRequestMode = "deny"
    interactive_request_timeout_seconds: int = 60


@dataclass(frozen=True)
class CapturePolicy:
    """Local history/event capture policy.

    ``standard`` is intentionally useful by default while raw provider payloads
    stay gated by retention policy. ``debug`` is a developer posture and should
    be visible in diagnostics because it can retain more provider data.
    """

    mode: CaptureMode = "standard"
    raw_payload_retention: RawPayloadRetention = "debug"
    max_text_bytes: int = 8192
    max_payload_bytes: int = 65536

    @property
    def raw_payloads_enabled(self) -> bool:
        return self.retains_any_raw_payloads

    @property
    def retains_any_raw_payloads(self) -> bool:
        return self.raw_payload_retention == "all" or (
            self.raw_payload_retention == "errors"
            or (self.raw_payload_retention == "debug" and self.mode == "debug")
        )

    def should_retain_raw_payload(self, *, is_error: bool = False) -> bool:
        if self.raw_payload_retention == "all":
            return True
        if self.raw_payload_retention == "errors":
            return is_error
        return self.raw_payload_retention == "debug" and self.mode == "debug"


def runtime_policy() -> RuntimePolicy:
    """Read local policy from env and ``~/.dispatch/config.toml``.

    Env wins so test and one-shot operator shells can force policy without
    mutating the user's config file.
    """
    path = config_path()
    policy = RuntimePolicy()
    if not path.exists():
        return _apply_env_policy(policy)

    with path.open("rb") as f:
        raw = tomllib.load(f)
    raw_policy = raw.get("policy", raw)
    if not isinstance(raw_policy, dict):
        return _apply_env_policy(policy)
    policy = RuntimePolicy(
        allow_attached_writes=bool(raw_policy.get("allow_attached_writes", False)),
        allow_workspace_setup=bool(raw_policy.get("allow_workspace_setup", False)),
        workspace_setup_timeout_seconds=_positive_int(
            raw_policy.get("workspace_setup_timeout_seconds"), default=120
        ),
        owned_interactive_requests=_interactive_request_mode(
            raw_policy.get("owned_interactive_requests"),
            name="owned_interactive_requests",
            default=policy.owned_interactive_requests,
        ),
        attached_interactive_requests=_interactive_request_mode(
            raw_policy.get("attached_interactive_requests"),
            name="attached_interactive_requests",
            default=policy.attached_interactive_requests,
        ),
        interactive_request_timeout_seconds=_positive_int(
            raw_policy.get("interactive_request_timeout_seconds"), default=60
        ),
    )
    return _apply_env_policy(policy)


def capture_policy() -> CapturePolicy:
    """Read local history capture policy from env and ``~/.dispatch/config.toml``."""
    path = config_path()
    policy = CapturePolicy()
    if not path.exists():
        return _apply_env_capture(policy)

    with path.open("rb") as f:
        raw = tomllib.load(f)
    raw_history = raw.get("history", {})
    if not isinstance(raw_history, dict):
        return _apply_env_capture(policy)
    policy = CapturePolicy(
        mode=_capture_mode(raw_history.get("capture"), default=policy.mode),
        raw_payload_retention=_raw_payload_retention(
            raw_history.get("raw_payload_retention"),
            default=policy.raw_payload_retention,
        ),
        max_text_bytes=_capture_positive_int(
            raw_history.get("max_text_bytes"),
            name="max_text_bytes",
            default=8192,
        ),
        max_payload_bytes=_capture_positive_int(
            raw_history.get("max_payload_bytes"),
            name="max_payload_bytes",
            default=65536,
        ),
    )
    return _apply_env_capture(policy)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_policy(policy: RuntimePolicy) -> RuntimePolicy:
    attached = os.environ.get("DISPATCH_ALLOW_ATTACHED_WRITES")
    return RuntimePolicy(
        allow_attached_writes=(
            policy.allow_attached_writes if attached is None else _truthy(attached)
        ),
        allow_workspace_setup=policy.allow_workspace_setup,
        workspace_setup_timeout_seconds=policy.workspace_setup_timeout_seconds,
        owned_interactive_requests=_interactive_request_mode(
            os.environ.get("DISPATCH_OWNED_INTERACTIVE_REQUESTS"),
            name="owned_interactive_requests",
            default=policy.owned_interactive_requests,
        ),
        attached_interactive_requests=_interactive_request_mode(
            os.environ.get("DISPATCH_ATTACHED_INTERACTIVE_REQUESTS"),
            name="attached_interactive_requests",
            default=policy.attached_interactive_requests,
        ),
        interactive_request_timeout_seconds=_positive_int(
            os.environ.get("DISPATCH_INTERACTIVE_REQUEST_TIMEOUT_SECONDS"),
            default=policy.interactive_request_timeout_seconds,
        ),
    )


def _apply_env_capture(policy: CapturePolicy) -> CapturePolicy:
    return CapturePolicy(
        mode=_capture_mode(os.environ.get("DISPATCH_CAPTURE"), default=policy.mode),
        raw_payload_retention=_raw_payload_retention(
            os.environ.get("DISPATCH_RAW_PAYLOAD_RETENTION"),
            default=policy.raw_payload_retention,
        ),
        max_text_bytes=_capture_positive_int(
            os.environ.get("DISPATCH_CAPTURE_MAX_TEXT_BYTES"),
            name="max_text_bytes",
            default=policy.max_text_bytes,
        ),
        max_payload_bytes=_capture_positive_int(
            os.environ.get("DISPATCH_CAPTURE_MAX_PAYLOAD_BYTES"),
            name="max_payload_bytes",
            default=policy.max_payload_bytes,
        ),
    )


def _capture_mode(value: object, *, default: CaptureMode) -> CaptureMode:
    if value is None:
        return default
    if isinstance(value, str) and value in {"minimal", "standard", "debug"}:
        return cast(CaptureMode, value)
    raise ValueError("history.capture must be one of: minimal, standard, debug")


def _raw_payload_retention(value: object, *, default: RawPayloadRetention) -> RawPayloadRetention:
    if value is None:
        return default
    if isinstance(value, str) and value in {"off", "errors", "debug", "all"}:
        return cast(RawPayloadRetention, value)
    raise ValueError("history.raw_payload_retention must be one of: off, errors, debug, all")


def _interactive_request_mode(
    value: object,
    *,
    name: str,
    default: InteractiveRequestMode,
) -> InteractiveRequestMode:
    if value is None:
        return default
    if isinstance(value, str) and value in {"deny", "attention", "permissive"}:
        return cast(InteractiveRequestMode, value)
    raise ValueError(f"policy.{name} must be one of: deny, attention, permissive")


def _capture_positive_int(value: object, *, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = int(value)
            if parsed > 0:
                return parsed
    raise ValueError(f"history.{name} must be a positive integer")


def _positive_int(value: object, *, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = int(value)
            if parsed > 0:
                return parsed
    return default
