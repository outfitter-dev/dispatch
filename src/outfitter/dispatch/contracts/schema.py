"""Shared schema projection helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def is_internal_field(field: object) -> bool:
    extra = getattr(field, "json_schema_extra", None)
    return isinstance(extra, dict) and extra.get("x-dispatch-internal") is True


def public_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        internal = {
            name
            for name, prop in properties.items()
            if isinstance(prop, dict) and prop.get("x-dispatch-internal") is True
        }
        for name in internal:
            del properties[name]
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [name for name in required if name not in internal]
    return schema


def inline_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline Pydantic's local definitions for a self-contained schema fragment."""
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return schema

    def walk(value: Any, stack: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, list):
            return [walk(item, stack) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.removeprefix("#/$defs/")
            target = definitions.get(name)
            if isinstance(target, dict) and name not in stack:
                merged = deepcopy(target)
                merged.update({key: item for key, item in value.items() if key != "$ref"})
                return walk(merged, stack | {name})
        projected = {key: walk(item, stack) for key, item in value.items() if key != "$defs"}
        discriminator = projected.get("discriminator")
        if isinstance(discriminator, dict):
            discriminator.pop("mapping", None)
        return projected

    result = walk(schema)
    if not isinstance(result, dict):
        raise TypeError("schema root must remain an object")
    return result
