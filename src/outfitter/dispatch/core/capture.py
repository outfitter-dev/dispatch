"""Bounded provider history capture helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from outfitter.dispatch.config import CapturePolicy


@dataclass(frozen=True)
class BoundedText:
    text: str
    original_bytes: int
    truncated: bool


@dataclass(frozen=True)
class BoundedPayload:
    payload: dict[str, object] | None
    original_bytes: int
    truncated: bool


def bound_text(text: str | None, policy: CapturePolicy) -> BoundedText | None:
    if text is None:
        return None
    encoded = text.encode("utf-8")
    if len(encoded) <= policy.max_text_bytes:
        return BoundedText(text=text, original_bytes=len(encoded), truncated=False)
    return BoundedText(
        text=encoded[: policy.max_text_bytes].decode("utf-8", errors="ignore"),
        original_bytes=len(encoded),
        truncated=True,
    )


def bound_payload(payload: dict[str, object] | None, policy: CapturePolicy) -> BoundedPayload:
    if payload is None:
        return BoundedPayload(payload=None, original_bytes=0, truncated=False)
    safe_payload = cast(dict[str, object], _json_safe(payload))
    encoded = _dump(safe_payload).encode("utf-8")
    if len(encoded) <= policy.max_payload_bytes:
        return BoundedPayload(
            payload=safe_payload,
            original_bytes=len(encoded),
            truncated=False,
        )
    preview = _bounded_preview(encoded, len(encoded), policy.max_payload_bytes)
    return BoundedPayload(
        payload=preview,
        original_bytes=len(encoded),
        truncated=True,
    )


def _dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _bounded_preview(
    encoded: bytes, original_bytes: int, max_bytes: int
) -> dict[str, object] | None:
    payload: dict[str, object] = {
        "truncated": True,
        "original_bytes": original_bytes,
        "preview": "",
    }
    overhead = len(_dump(payload).encode("utf-8"))
    if overhead <= max_bytes:
        payload["preview"] = encoded[: max_bytes - overhead].decode("utf-8", errors="ignore")
        while len(_dump(payload).encode("utf-8")) > max_bytes and payload["preview"]:
            payload["preview"] = str(payload["preview"])[:-1]
        return payload

    marker: dict[str, object] = {"truncated": True}
    if len(_dump(marker).encode("utf-8")) <= max_bytes:
        return marker
    return None


def _json_safe(value: Any) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_safe(child) for child in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
