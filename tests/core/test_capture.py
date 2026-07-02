from __future__ import annotations

import json

from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.core.capture import bound_payload, bound_text


def test_bound_text_tracks_original_size_without_truncating() -> None:
    bounded = bound_text("hello", CapturePolicy(max_text_bytes=10))

    assert bounded is not None
    assert bounded.text == "hello"
    assert bounded.original_bytes == 5
    assert bounded.truncated is False


def test_bound_text_truncates_by_utf8_bytes() -> None:
    bounded = bound_text("hello world", CapturePolicy(max_text_bytes=5))

    assert bounded is not None
    assert bounded.text == "hello"
    assert bounded.original_bytes == 11
    assert bounded.truncated is True


def test_bound_payload_keeps_small_json_safe_payload() -> None:
    bounded = bound_payload({"b": object(), "a": ["ok"]}, CapturePolicy(max_payload_bytes=80))

    assert bounded.truncated is False
    assert bounded.payload is not None
    assert bounded.payload["a"] == ["ok"]
    assert isinstance(bounded.payload["b"], str)
    assert bounded.original_bytes > 0


def test_bound_payload_truncates_large_payload_with_metadata() -> None:
    bounded = bound_payload({"text": "x" * 100}, CapturePolicy(max_payload_bytes=20))

    assert bounded.truncated is True
    assert bounded.original_bytes > 20
    assert bounded.payload is not None
    assert bounded.payload["truncated"] is True
    retained_bytes = len(json.dumps(bounded.payload, separators=(",", ":")).encode("utf-8"))
    assert retained_bytes <= 20


def test_bound_payload_retains_metadata_when_it_fits() -> None:
    bounded = bound_payload({"text": "x" * 100}, CapturePolicy(max_payload_bytes=80))

    assert bounded.truncated is True
    assert bounded.payload is not None
    assert bounded.payload["truncated"] is True
    assert bounded.payload["original_bytes"] == bounded.original_bytes
    assert isinstance(bounded.payload["preview"], str)
    retained_bytes = len(json.dumps(bounded.payload, separators=(",", ":")).encode("utf-8"))
    assert retained_bytes <= 80
