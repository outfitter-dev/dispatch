"""Client-layer exceptions.

The ``client/`` package is importable standalone (no daemon/contracts
dependency, per ``.claude/rules/client.md``), so it defines its own minimal
exception family. Phase 2's ``contracts/errors.py`` (the ``DispatchError``
taxonomy, ADR-0001) maps these into surface-projected errors at the handler
boundary; nothing here imports from ``contracts``.
"""

from __future__ import annotations


class ClientError(Exception):
    """Base class for all App Server client errors."""


class TransportError(ClientError):
    """The app-server subprocess/stream failed (spawn failure, stdout EOF, write
    to a dead process)."""


class ProtocolError(ClientError):
    """A wire message was malformed or violated the expected JSON-RPC-lite shape."""


class AppServerError(ClientError):
    """The app-server returned a JSON-RPC error response to one of our requests."""

    def __init__(self, code: int, message: str, data: object | None = None) -> None:
        super().__init__(f"app-server error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data
