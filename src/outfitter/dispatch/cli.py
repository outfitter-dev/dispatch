"""dispatch CLI entrypoint (console script ``dispatch``).

The app is *derived* from the op registry — there are no hand-written per-op
commands here (see ``surfaces/cli.py`` and ``contracts/derive_cli.py``). Adding
an op makes it appear on the CLI automatically.
"""

from __future__ import annotations

from outfitter.dispatch.surfaces.cli import build_cli

app = build_cli()


if __name__ == "__main__":
    app()
