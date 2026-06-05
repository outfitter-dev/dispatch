"""Shared schema projection helpers."""

from __future__ import annotations

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
