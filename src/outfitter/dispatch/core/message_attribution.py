"""Shared attribution footer for Dispatch-originated visible turns."""

from __future__ import annotations

from collections.abc import Iterable

DISPATCH_FOOTER_PREFIX = "dispatch"
DISPATCH_DETAIL_PREFIX = "↳"


def render_dispatch_message(
    *,
    body: str,
    kind: str,
    source: str,
    ref: str,
    details: Iterable[str | None] = (),
) -> str:
    """Append a compact Dispatch attribution footer to a visible turn."""
    footer = f"{DISPATCH_FOOTER_PREFIX} ({kind}): {source} `{ref}`"
    detail_text = " | ".join(detail for detail in details if detail)
    if detail_text:
        footer = f"{footer}\n{DISPATCH_DETAIL_PREFIX} {detail_text}"
    clean_body = body.strip()
    return f"{clean_body}\n\n{footer}" if clean_body else footer


def codex_thread_link(label: str, thread_id: str) -> str:
    """Return a Markdown link to a Codex desktop thread."""
    return f"[{label}](codex://threads/{thread_id})"
