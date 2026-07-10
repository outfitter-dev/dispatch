# dispatch task runner. Always go through uv (never bare python/pip).

# Run the full quality gate: lint + format check + strict types + tests.
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy src tests
    uv run pytest
    rm -f dist/*.whl dist/*.tar.gz
    uv build
    uv run python scripts/check_package_contents.py

# Run the test suite (unit only; integration is deselected by default).
test *args:
    uv run pytest {{args}}

# Run integration tests against a real ephemeral codex app-server.
test-int *args:
    uv run pytest -m integration {{args}}

# Smoke-test the published PyPI package from a clean temporary DISPATCH_HOME.
pypi-smoke *args:
    uv run python scripts/check_pypi_smoke.py {{args}}

# Run a live agent scenario against an isolated DISPATCH_HOME/CODEX_HOME.
scenario *args:
    uv run python scripts/run_scenario.py {{args}}

# Refresh the compact App Server compatibility manifest from generated schemas.
app-server-manifest *args:
    uv run python scripts/refresh_app_server_manifest.py {{args}}

# Lint with ruff.
lint:
    uv run ruff check .

# Format the tree with ruff.
fmt:
    uv run ruff format .

# Type-check with mypy --strict.
typecheck:
    uv run mypy src tests

# Run the dispatch CLI in-tree, e.g. `just run -- --help`.
run *args:
    uv run dispatch {{args}}
