"""The op registry — the durable collection every surface projects from.

Authoring a capability = adding an op + registering it here. Nothing else.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from functools import lru_cache

from .op import Op

CONTROL_META_METHOD = "__dispatch/metadata"
"""Control-socket handshake method: reports version, supported ops, and per-op
schema fingerprints so clients can detect a stale daemon before forwarding
input the daemon would silently ignore."""


class OpRegistry:
    """An ordered, name-unique collection of ops."""

    def __init__(self) -> None:
        self._ops: dict[str, Op] = {}

    def register(self, op: Op) -> Op:
        if op.id in self._ops:
            raise ValueError(f"duplicate op id: {op.id!r}")
        self._ops[op.id] = op
        return op

    def get(self, op_id: str) -> Op:
        try:
            return self._ops[op_id]
        except KeyError:
            raise KeyError(f"unknown op id: {op_id!r}") from None

    def ids(self) -> list[str]:
        return list(self._ops)

    def __iter__(self) -> Iterator[Op]:
        return iter(self._ops.values())

    def __len__(self) -> int:
        return len(self._ops)


def op_schema_hash(op: Op) -> str:
    """Fingerprint one op's input/output JSON schema.

    Two processes agree on an op's hash iff they accept and produce the same
    shapes for it — a field added to the op (which Pydantic's default
    ``extra="ignore"`` would otherwise drop silently on the older side) changes
    the hash.
    """
    payload = {
        "input": op.input.model_json_schema(),
        "output": op.output.model_json_schema(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=4)
def registry_op_schema_hashes(registry: OpRegistry) -> dict[str, str]:
    """Per-op schema fingerprints (op id → :func:`op_schema_hash`).

    Reported in the control-socket handshake so clients can block only the ops
    whose schemas actually drifted. Cached by registry identity; the registry
    is a process-lifetime singleton.
    """
    return {op.id: op_schema_hash(op) for op in registry}


@lru_cache(maxsize=4)
def registry_read_safe_ops(registry: OpRegistry) -> frozenset[str]:
    """Read ops eligible for the pre-handshake read allowance.

    Read-intent ops whose input model is shared with no non-read op: sharing
    input with a write (``new-plan`` previews ``new`` through the same
    ``NewInput``) means the read op carries exactly the skew-sensitive fields
    the write op is gated on, and a legacy daemon would silently drop the
    ones it does not know, misreporting what the real op would do.

    Eligibility, not a blanket pass: read inputs also evolve (v0.8.1's
    ``RosterInput`` had no ``parent``), so :func:`prehandshake_op_allowed`
    additionally requires the daemon to self-report at least
    ``READ_BASELINE_FLOOR`` — the oldest release whose read schemas are proven
    identical to current (evidence in :mod:`.legacy_baseline`).
    """
    gated_inputs = {op.input for op in registry if op.intent != "read"}
    return frozenset(
        op.id for op in registry if op.intent == "read" and op.input not in gated_inputs
    )


@lru_cache(maxsize=4)
def registry_legacy_safe_ops(registry: OpRegistry) -> frozenset[str]:
    """Ops safe to forward to a daemon that predates the op-schema handshake.

    Such a daemon reports no per-op fingerprints, so clients cannot verify
    field-level drift at runtime. An op qualifies two ways, both derived:

    - **Read-safe:** see :func:`registry_read_safe_ops`. Allowed against a
      pre-handshake daemon reporting at least ``READ_BASELINE_FLOOR``.
    - **Baseline-matching:** its current :func:`op_schema_hash` equals the
      parent release's recorded hash (:mod:`.legacy_baseline`) — the schema is
      unchanged since the release a pre-handshake daemon runs, so that daemon
      parses the input identically. This keeps schema-stable control ops
      (``stop``, ``send``, ...) usable to drain or steer a running
      pre-handshake daemon right after an upgrade — exactly when an operator
      most needs them. Ops whose schemas drifted (``new``/``new-plan`` gained
      ``provider``) carry no baseline hash and stay blocked. The baseline
      proves parity ONLY with the parent release, so surfaces apply this
      allowance only when the daemon self-reports exactly ``PARENT_VERSION``
      (see :func:`prehandshake_op_allowed`) — an older daemon's schemas may
      differ from the baseline (v0.8.2's ``send`` had no ``content``).

    Membership is derived from the registry and the checked-in baseline, never
    hand-listed here; the baseline test forces a conscious
    ``CHANGED_SINCE_PARENT`` update whenever an op's schema drifts.
    """
    from .legacy_baseline import PARENT_OP_SCHEMA_HASHES

    return registry_read_safe_ops(registry) | frozenset(
        op.id for op in registry if op_schema_hash(op) == PARENT_OP_SCHEMA_HASHES.get(op.id)
    )


def _version_tuple(version: object) -> tuple[int, ...] | None:
    """Parse a plain ``X.Y.Z`` release string into a comparable tuple.

    Anything else (non-string, empty, dev/rc suffixes) parses to ``None`` so
    the caller fails closed. Every published release self-reports the plain
    form; ``packaging`` is deliberately not pulled in for this.
    """
    if not isinstance(version, str):
        return None
    parts = version.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def prehandshake_op_allowed(
    reported_version: object, *, read_safe: bool, baseline_safe: bool
) -> bool:
    """Whether an op may be forwarded to a daemon reporting no per-op hashes.

    ``reported_version`` is the ``version`` from the daemon's
    ``__dispatch/metadata`` response — every release that has the method at
    all reports it; a daemon without the method reports nothing (``None``).

    Read-safe ops pass when the daemon self-reports at least
    ``READ_BASELINE_FLOOR``, the oldest release whose read-safe op schemas
    are proven identical to current (sdist evidence in
    :mod:`.legacy_baseline`) — older read inputs drifted too (v0.8.1's
    ``roster`` had no ``parent``), so a version below the floor, an
    unparseable version, or no version at all (no metadata method: <= 0.8.1)
    blocks even reads. Baseline-matching ops are proven identical ONLY
    against the parent release, so they require the daemon to self-report
    exactly ``PARENT_VERSION``: an older daemon (e.g. a long-running v0.10
    one) may parse the same op differently and Pydantic's default
    ``extra="ignore"`` would drop fields silently.
    """
    from .legacy_baseline import PARENT_VERSION, READ_BASELINE_FLOOR

    if read_safe:
        reported = _version_tuple(reported_version)
        floor = _version_tuple(READ_BASELINE_FLOOR)
        return reported is not None and floor is not None and reported >= floor
    if not baseline_safe:
        return False
    return reported_version == PARENT_VERSION


@lru_cache(maxsize=4)
def registry_schema_hash(registry: OpRegistry) -> str:
    """Whole-registry fingerprint over the per-op hashes (diagnostic summary)."""
    payload = registry_op_schema_hashes(registry)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
