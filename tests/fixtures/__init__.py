"""Shared fixture loading helpers.

Fixtures are intentionally data-first. Tests import these helpers so fixture
paths stay stable while individual test modules remain focused on behavior.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

JsonObject = dict[str, object]

FIXTURE_ROOT = Path(__file__).resolve().parent


def fixture_path(*parts: str) -> Path:
    return FIXTURE_ROOT.joinpath(*parts)


def load_json(*parts: str) -> JsonObject:
    data = json.loads(fixture_path(*parts).read_text())
    if not isinstance(data, dict):
        raise AssertionError(f"expected JSON object fixture: {'/'.join(parts)}")
    return cast(JsonObject, data)


def load_jsonl(*parts: str) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for line_number, line in enumerate(fixture_path(*parts).read_text().splitlines(), start=1):
        if not line:
            continue
        data = json.loads(line)
        if not isinstance(data, dict):
            raise AssertionError(f"expected JSON object at {'/'.join(parts)}:{line_number}")
        rows.append(cast(JsonObject, data))
    return rows


def copy_fixture(*parts: str, to: Path) -> Path:
    to.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture_path(*parts), to)
    return to
