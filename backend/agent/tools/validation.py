"""Fail-closed validation for LLM-generated tool arguments."""

from typing import Any


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def validate_tool_arguments(schema: dict | None, arguments: dict[str, Any]) -> None:
    """Raise ValueError when arguments violate a small, strict JSON Schema subset."""
    if not schema:
        return
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    if schema.get("type", "object") != "object":
        return

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    missing = [name for name in required if name not in arguments]
    if missing:
        raise ValueError(f"Missing required arguments: {', '.join(sorted(missing))}")

    for name, value in arguments.items():
        prop = properties.get(name)
        if prop is None:
            raise ValueError(f"Unknown argument: {name}")
        expected_type = prop.get("type")
        if expected_type and not _type_matches(value, expected_type):
            raise ValueError(f"Invalid type for argument {name}: expected {expected_type}")
