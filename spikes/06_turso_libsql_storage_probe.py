"""Probe Dispatch registry SQL compatibility against Turso/libSQL packages.

This is an explicit spike, not production code. Run with:

    uv run --with pyturso --with libsql python spikes/06_turso_libsql_storage_probe.py

It answers a narrow question: can the current registry schema and representative
history writes run through sqlite-like Turso/libSQL DB-API connections without
touching Dispatch's default aiosqlite store?
"""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable
from typing import Any

from outfitter.dispatch.registry.sql_compat import (
    SqlCompatibilityResult,
    run_sql_compat_probe,
)


def main() -> None:
    results = [
        run_sql_compat_probe("sqlite3", lambda path: sqlite3.connect(path)),
        _run_optional_backend("pyturso", "turso", lambda module, path: module.connect(path)),
        _run_optional_backend("libsql", "libsql", lambda module, path: module.connect(path)),
    ]
    width = max(len(result.backend) for result in results)
    for result in results:
        print(
            f"{result.backend:<{width}}  {result.status:<6}  "
            f"{result.elapsed_ms:8.2f} ms  {result.detail}"
        )
    if any(result.status == "FAIL" for result in results):
        raise SystemExit(1)


def _run_optional_backend(
    backend: str,
    module_name: str,
    connect: Callable[[Any, str], Any],
) -> SqlCompatibilityResult:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return SqlCompatibilityResult(backend, "SKIP", 0.0, f"install with --with {backend}")
    return run_sql_compat_probe(backend, lambda path: connect(module, path))


if __name__ == "__main__":
    main()
