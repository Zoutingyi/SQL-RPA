"""SQL identifier and literal helpers shared by SQLite and MySQL connectors."""

import re

USER_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DANGEROUS_CONDITION_RE = re.compile(
    r"(--|/\*|\*/|;|\bUNION\b|\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b|"
    r"\bDROP\b|\bALTER\b|\bTRUNCATE\b|\bEXEC\b)",
    re.IGNORECASE,
)


def is_valid_user_identifier(value: str) -> bool:
    """Return True when a user-supplied identifier matches a conservative allowlist."""
    return bool(USER_IDENTIFIER_RE.match(value))


def validate_user_identifier(value: str, label: str = "identifier") -> str:
    """Validate a user-supplied identifier and return the stripped value."""
    if not isinstance(value, str) or not USER_IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def validate_where_condition(condition: str) -> str:
    """Reject common stacked-query / comment / nested-statement patterns."""
    if not isinstance(condition, str) or not condition.strip():
        raise ValueError("WHERE condition cannot be empty")
    if DANGEROUS_CONDITION_RE.search(condition):
        raise ValueError("WHERE condition contains disallowed SQL patterns")
    return condition


def quote_identifier(identifier: str, dialect: str) -> str:
    """Quote an already-trusted database identifier for a dialect.

    This function does not decide whether an identifier is trusted. Callers must
    validate user input first, then pass a real table/column name here.
    """
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("Identifier must be a non-empty string")
    if "\x00" in identifier:
        raise ValueError("Identifier contains NUL byte")

    if dialect == "mysql":
        return f"`{identifier.replace('`', '``')}`"
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'
