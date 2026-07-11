from __future__ import annotations

import json

from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.core.capture import (
    bound_payload,
    bound_redacted_json,
    bound_redacted_text,
    bound_text,
)


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


def test_normalized_fields_redact_secrets_and_respect_bounds() -> None:
    policy = CapturePolicy(max_text_bytes=32, max_payload_bytes=96)
    text = bound_redacted_text("--token supersecret " + "x" * 100, policy)
    arguments = bound_redacted_json(
        {
            "token": "supersecret",
            "nested": {"authorization": "Bearer hidden"},
            "prompt": "password=hunter2 " + "x" * 200,
        },
        policy,
    )

    assert text is not None
    assert "supersecret" not in text.text
    assert len(text.text.encode()) <= 32
    encoded = json.dumps(arguments, separators=(",", ":")).encode()
    assert b"supersecret" not in encoded
    assert b"hunter2" not in encoded
    assert len(encoded) <= 96


def test_payload_capture_never_retains_inline_image_bytes_or_secret_keys() -> None:
    bounded = bound_payload(
        {
            "url": "data:image/png;base64,c2VjcmV0LWltYWdlLWJ5dGVz",
            "token": "secret-token",
        },
        CapturePolicy(max_payload_bytes=1024),
    )

    encoded = json.dumps(bounded.payload)
    assert "c2VjcmV0" not in encoded
    assert "secret-token" not in encoded
    assert "[image data omitted]" in encoded
