"""JSON Schema loader and validator for raw_session.schema.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:
    jsonschema = None

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "schemas" / "raw_session.schema.json"
_schema_cache: dict[str, Any] | None = None


def get_schema() -> dict[str, Any]:
    """Load and cache the raw_session JSON Schema."""
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return _schema_cache


def validate_raw_session(data: dict[str, Any]) -> list[str]:
    """Validate a raw session dict against the schema.

    Returns a list of error messages (empty if valid).
    If jsonschema is not installed, returns an empty list (skip validation).
    """
    if jsonschema is None:
        return []
    schema = get_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [f"{'.'.join(str(p) for p in e.path)}: {e.message}" for e in errors]
