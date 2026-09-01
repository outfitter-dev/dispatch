"""Deprecated capture-only stdin entrypoint for Claude Code statuslines.

``dispatch-claude-statusline`` is retained for at least one release for the
documented wrapper pattern: users call it from inside their own statusline
script, then run their own renderer. It captures and emits nothing — it must
never delegate to the recorded original renderer, because a recorded wrapper
that itself invokes this helper would recurse on every statusline refresh.
Keep the documented wrapper pattern until ``dispatch usage-capture install``
ships and manages the statusline integration. Do not hand-point
``statusLine.command`` at ``dispatch usage-capture run``: without a managed
restoration record it captures, emits no stdout, and silently drops any
renderer the wrapper used to chain.
"""

from __future__ import annotations

import sys

from outfitter.dispatch.core.usage_capture_run import MAX_STDIN_BYTES, capture_snapshot

_HELP = """\
usage: dispatch-claude-statusline

Deprecated capture-only helper. Reads Claude Code's statusline JSON from
stdin, captures normalized rate-limit facts, and emits nothing — call it
from your own statusline wrapper, then run your renderer. It never
delegates to a recorded original renderer (a wrapper invoking this helper
would recurse). Keep this wrapper pattern until the managed
`dispatch usage-capture install` integration ships; pointing
statusLine.command directly at `dispatch usage-capture run` renders
nothing without a managed restoration record."""


def main() -> None:
    """Capture one snapshot from stdin; never render or delegate."""

    if any(flag in sys.argv[1:] for flag in ("-h", "--help")):
        print(_HELP)
        return
    capture_snapshot(sys.stdin.buffer.read(MAX_STDIN_BYTES + 1))
    raise SystemExit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
