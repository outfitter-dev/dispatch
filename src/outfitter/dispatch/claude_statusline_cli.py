"""Minimal stdin capture entrypoint for Claude Code statuslines."""

from __future__ import annotations

import sys

from outfitter.dispatch.core.claude_statusline import (
    StatuslineCaptureError,
    capture_claude_statusline,
)

_MAX_STDIN_BYTES = 1024 * 1024


def main() -> None:
    """Capture one normalized snapshot without producing statusline output."""

    try:
        payload = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
        capture_claude_statusline(payload)
    except (OSError, StatuslineCaptureError):
        print("dispatch: Claude statusline snapshot rejected", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":  # pragma: no cover
    main()
