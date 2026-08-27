"""SQL safety rule engine. Classifies SQL statements and returns safety verdicts."""

import re
from dataclasses import dataclass, field

CRITICAL_KEYWORDS = ("DROP", "TRUNCATE", "ALTER")
WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "REPLACE")
READ_KEYWORDS = ("SELECT", "PRAGMA", "EXPLAIN", "DESCRIBE", "SHOW")
FULL_SCAN_WARN_THRESHOLD = 100_000
LARGE_RESULT_THRESHOLD = 5_000

DENIED_SQL_PATTERNS = (
    r"\bINTO\s+(OUTFILE|DUMPFILE)\b",
    r"\bLOAD_FILE\s*\(",
    r"\bSLEEP\s*\(",
    r"\bBENCHMARK\s*\(",
    r"\bATTACH\s+DATABASE\b",
    r"\bDETACH\s+DATABASE\b",
    r"\bCOPY\s+.+\s+(FROM|TO)\s+PROGRAM\b",
)


@dataclass
class SafetyResult:
    allowed: bool = True
    blocked: bool = False
    requires_review: bool = False
    level: str = "info"       # info | warning | danger | critical
    message: str = ""
    warnings: list[str] = field(default_factory=list)


class SafetyChecker:
    """Analyzes a SQL string and returns a SafetyResult classifying the risk level."""

    def check(self, sql: str, table_row_counts: dict[str, int] | None = None) -> SafetyResult:
        stripped = sql.strip()
        upper = stripped.upper()

        # Fail closed on common database-file / remote-command escape patterns.
        for pattern in DENIED_SQL_PATTERNS:
            if re.search(pattern, upper, re.IGNORECASE):
                return SafetyResult(
                    allowed=False,
                    blocked=True,
                    level="critical",
                    message="SQL contains a disallowed dangerous function or statement.",
                )

        # 0. Multi-statement detection
        statements = [s for s in stripped.split(";") if s.strip()]
        if len(statements) > 1:
            return SafetyResult(
                allowed=False, blocked=True, level="critical",
                message="Multiple SQL statements are not allowed. Submit each statement separately.",
            )

        if not upper.split():
            return SafetyResult(allowed=False, blocked=True, level="danger", message="Empty SQL statement.")

        first_word = upper.split()[0]

        # 1. Read-only queries — allow directly
        if first_word in READ_KEYWORDS:
            result = SafetyResult(allowed=True, level="info", message="Read-only query, allowed.")
            if table_row_counts:
                result = self._check_full_scan(sql, table_row_counts, result)
            return result

        # 2. Dangerous DDL — block entirely
        if first_word in CRITICAL_KEYWORDS:
            return SafetyResult(
                allowed=False, blocked=True, level="critical",
                message=(
                    f"Dangerous operation '{first_word}' is blocked. "
                    f"Dropping, truncating, or altering tables can cause irreversible data loss. "
                    f"Contact an administrator to perform this operation manually."
                ),
            )

        # 3. Write operations — require review
        if first_word in WRITE_KEYWORDS:
            from config import settings
            if first_word in ("DELETE", "UPDATE") and settings.review_where_required:
                no_where = self._check_has_where(sql, first_word)
                if no_where:
                    return no_where

            level_map = {"INSERT": "info", "UPDATE": "warning", "DELETE": "danger", "REPLACE": "warning"}
            return SafetyResult(
                allowed=True, requires_review=True,
                level=level_map.get(first_word, "warning"),
                message=(
                    f"This {first_word} operation requires review. "
                    f"Affected data will be automatically backed up before execution."
                ),
            )

        # 4. Unknown SQL type — reject
        return SafetyResult(
            allowed=False, blocked=True, level="danger",
            message=f"Unsupported SQL type: {first_word}. Only SELECT/INSERT/UPDATE/DELETE are allowed.",
        )

    # ── Private helpers ──

    def _check_has_where(self, sql: str, op_type: str) -> SafetyResult | None:
        """Reject DELETE/UPDATE without a WHERE clause. Returns None if WHERE is present and meaningful."""
        upper = sql.upper()
        if not re.search(r'\bWHERE\b', upper):
            return SafetyResult(
                allowed=False, blocked=True, level="danger",
                message=(
                    f"{op_type} without a WHERE clause would affect ALL rows in the table. "
                    f"Please add a specific WHERE condition to scope the operation."
                ),
            )
        # Detect trivial WHERE like "WHERE 1=1" or "WHERE true"
        trivial = re.search(r'\bWHERE\s+(1\s*=\s*1|true|1)\s*(?:ORDER|LIMIT|GROUP|HAVING|;|$)', upper)
        if trivial:
            return SafetyResult(
                allowed=False, blocked=True, level="danger",
                message="WHERE 1=1 is equivalent to no condition. Specify a meaningful filter clause.",
            )
        return None

    def _check_full_scan(self, sql: str, table_row_counts: dict[str, int], result: SafetyResult) -> SafetyResult:
        """Warn about SELECT * on large tables without WHERE/LIMIT."""
        upper = sql.upper()
        if "WHERE" in upper or "LIMIT" in upper:
            return result

        match = re.search(r'\bFROM\s+["`\[]?(\w+)["`\]]?', upper, re.IGNORECASE)
        if match:
            table = match.group(1)
            count = table_row_counts.get(table, 0)
            if count > FULL_SCAN_WARN_THRESHOLD:
                result.warnings.append(
                    f"Table '{table}' has ~{count:,} rows. Full scan without WHERE/LIMIT "
                    f"may be slow. Consider adding a LIMIT or WHERE clause."
                )
        return result

    def check_result_size(self, row_count: int) -> list[str]:
        """Check if the result set exceeds the display threshold."""
        warnings: list[str] = []
        if row_count > LARGE_RESULT_THRESHOLD:
            warnings.append(
                f"Query returned {row_count:,} rows, exceeding the {LARGE_RESULT_THRESHOLD:,} "
                f"row display limit. Results will be truncated."
            )
        return warnings
