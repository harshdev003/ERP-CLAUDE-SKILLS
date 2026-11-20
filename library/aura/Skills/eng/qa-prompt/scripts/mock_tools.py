"""Generate a plausible mock value from a tool's `returns` JSON schema.

Used by auto_mock.py when the orchestrator picks "auto-mock from schema" in
response to a tool call. Walks the schema, honoring `enum`, `default`,
`required`, and best-effort `format`/`description` hints, and emits a
shape-valid JSON value. The RNG is seeded so the same schema always
produces the same mock — useful for stable diffs while iterating on a prompt.
"""
from __future__ import annotations

import random
from typing import Any


class SchemaGenerationError(RuntimeError):
    pass


# Deterministic RNG so a re-run with the same schema produces the same mock.
_RNG = random.Random(42)


def _sample_string(prop_schema: dict[str, Any]) -> str:
    """Pick a plausible string value from the schema's hints."""
    enum = prop_schema.get("enum")
    if isinstance(enum, list) and enum:
        return str(_RNG.choice(enum))
    desc = (prop_schema.get("description") or "").lower()
    fmt = (prop_schema.get("format") or "")
    if "uuid" in desc or "identifier" in desc or "id" in fmt:
        return "00000000-0000-4000-8000-000000000001"
    if "timestamp" in desc or fmt == "date-time":
        return "2026-01-01T00:00:00.000Z"
    if "url" in desc or fmt == "uri":
        return "https://example.com/mock"
    if "email" in desc or fmt == "email":
        return "mock@example.com"
    if "question" in desc or "recall" in desc:
        return "What feels alive for the user today?"
    return "mocked-string"


def _sample_value(schema: dict[str, Any]) -> Any:
    """Produce a single value matching the given JSON Schema fragment."""
    if not isinstance(schema, dict):
        return None

    # oneOf/anyOf: pick the first deterministically.
    for combinator in ("oneOf", "anyOf"):
        options = schema.get(combinator)
        if isinstance(options, list) and options:
            return _sample_value(options[0])

    # allOf: shallow merge then continue.
    if isinstance(schema.get("allOf"), list):
        merged: dict[str, Any] = {}
        for sub in schema["allOf"]:
            if isinstance(sub, dict):
                merged.update(sub)
        schema = {**merged, **{k: v for k, v in schema.items() if k != "allOf"}}

    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)

    if "default" in schema:
        return schema["default"]

    if t == "string":
        return _sample_string(schema)
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "null":
        return None
    if t == "array":
        items = schema.get("items") or {}
        return [_sample_value(items) for _ in range(2)]
    if t == "object" or "properties" in schema:
        return _sample_object(schema)

    return None


def _sample_object(schema: dict[str, Any]) -> dict[str, Any]:
    """Produce an object honoring `required` + `properties`."""
    out: dict[str, Any] = {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    optionals_to_keep = list(p for p in props.keys() if p not in required)[:3]
    keys = list(required) + optionals_to_keep
    for key in keys:
        prop = props.get(key)
        if isinstance(prop, dict):
            out[key] = _sample_value(prop)
    return out


def generate_from_schema(returns_schema: dict[str, Any]) -> Any:
    """Walk a tool's `returns` JSON schema and emit a plausible response."""
    if not isinstance(returns_schema, dict):
        raise SchemaGenerationError("returns_schema is not a dict")
    try:
        return _sample_value(returns_schema)
    except Exception as e:
        raise SchemaGenerationError(str(e)) from e
