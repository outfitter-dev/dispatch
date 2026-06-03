# dispatch task runner. Always go through uv (never bare python/pip).

# Run the full quality gate: lint + format check + strict types + tests.
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy src tests
    uv run pytest

# Run the test suite.
test *args:
    uv run pytest {{args}}

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
