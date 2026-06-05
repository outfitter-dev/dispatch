"""Dispatch-local short refs for managed Codex threads."""

from __future__ import annotations

from hashlib import sha256

BASE58BTC_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
CODEX_REF_SOURCE = "0"


def codex_ref_payload(thread_id: str) -> str:
    """Return the four-character base58btc hash payload for a Codex thread id."""

    digest = sha256(f"codex:{thread_id}".encode()).digest()
    return _base58btc(digest)[:4]


def make_ref(*, source: str, payload: str, mixer: str) -> str:
    return f"{source}{payload}{mixer}"


def _base58btc(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    encoded = ""
    while value:
        value, remainder = divmod(value, 58)
        encoded = BASE58BTC_ALPHABET[remainder] + encoded
    leading_zeroes = len(data) - len(data.lstrip(b"\0"))
    return (BASE58BTC_ALPHABET[0] * leading_zeroes) + (encoded or BASE58BTC_ALPHABET[0])
