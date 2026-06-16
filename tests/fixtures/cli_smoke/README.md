# CLI Smoke Fixtures

The reusable clean-install smoke lives in `scripts/check_pypi_smoke.py`. It uses
a temporary `DISPATCH_HOME`, installs the published package with `uvx`, starts a
daemon, verifies the model catalog path, verifies cached registry reads, and
shuts the daemon down.

`new_subscribe.json` contains stable argv/expectation cases for the `dispatch
new --subscribe` operator spellings. Keep networked or machine-specific outputs
out of git.
