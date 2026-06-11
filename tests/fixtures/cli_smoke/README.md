# CLI Smoke Fixtures

The reusable clean-install smoke lives in `scripts/check_pypi_smoke.py`. It uses
a temporary `DISPATCH_HOME`, installs the published package with `uvx`, starts a
daemon, verifies the model catalog path, verifies cached registry reads, and
shuts the daemon down.

This directory is reserved for future input/output fixtures if the smoke needs
stable golden payloads. Keep networked or machine-specific outputs out of git.

