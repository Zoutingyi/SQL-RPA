"""RPA database tools: get_db_schema, query_db, execute_sql."""

import re
from agent.tools import BaseTool, ToolResult
from agent.identity import get_actor_id, get_actor_role
from db_connector.sql_utils import validate_user_identifier
from utils.masking import mask_rows


# ── SQL helpers ──

_READONLY_PREFIXES = ("SELECT", "PRAGMA", "EXPLAIN", "WITH", "DESCRIBE", "SHOW")
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_MAX_ROWS = 500

_BLOCKED_IN_READONLY = (
    "DROP", "TRUNCATE", "ALTER", "CREATE TABLE", "CREATE INDEX",
    "INSERT", "DELETE", "UPDATE", "REPLACE", "GRANT", "REVOKE",
)


def _strip_strings(sql: str) -> str:
    """Remove string literals so we don't match keywords inside them."""
    return re.sub(r"'[^']*'", "''", sql)


def _is_readonly_sql(sql: str) -> bool:
    """Check if SQL is a read-only statement (no write keywords anywhere)."""
    cleaned = _strip_strings(sql).strip().upper()
    for kw in _BLOCKED_IN_READONLY:
        if re.search(rf"\b{kw}\b", cleaned):
            return False
    for prefix in _READONLY_PREFIXES:
        if cleaned.startswith(prefix):
            return True
    return False


def _validate_single_statement(sql: str) -> bool:
    """Ensure only one SQL statement (no stacked queries)."""
    cleaned = _strip_strings(sql).strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    return ";" not in cleaned


def _add_limit(sql: str) -> str:
    """Append LIMIT if not already present."""
    cleaned = sql.strip().rstrip(";").strip()
    if "LIMIT" not in cleaned.upper():
        return f"{cleaned} LIMIT {_MAX_ROWS}"
    return cleaned


def _extract_table_name(sql: str, op_type: str) -> str:
    """Extract the primary table name from a SQL statement."""
    patterns = {
        "SELECT": r'\bFROM\s+["`\[]?(\w+)["`\]]?',
        "INSERT": r'\bINTO\s+["`\[]?(\w+)["`\]]?',
        "UPDATE": r'\bUPDATE\s+["`\[]?(\w+)["`\]]?',
        "DELETE": r'\bFROM\s+["`\[]?(\w+)["`\]]?',
    }
    pattern = patterns.get(op_type)
    if not pattern:
        return "unknown"
    m = re.search(pattern, sql, re.IGNORECASE)
    return m.group(1) if m else "unknown"


_WHERE_INJECTION_RE = re.compile(
    r'(\bUNION\b|\bSELECT\b|--|\bOR\b\s+\d+\s*=\s*\d+|/\*|\*/|;)',
    re.IGNORECASE,
)

def _validate_where(where: str) -> None:
    """Reject WHERE clauses that contain injection payloads."""
    if _WHERE_INJECTION_RE.search(where):
        raise ValueError("WHERE clause contains disallowed SQL patterns")

def _extract_where(sql: str) -> str | None:
    """Extract the WHERE clause from SQL (the condition after WHERE keyword)."""
    m = re.search(
        r'\bWHERE\b\s+(.+?)(?:\s*(?:ORDER|LIMIT|GROUP|HAVING)\s|;|$)',
        sql, re.IGNORECASE | re.DOTALL,
    )
    where = m.group(1).strip().rstrip(";") if m else None
    if where:
        _validate_where(where)
    return where


# ═══════════════════════════════════════════════════════════════
# Tool: get_db_schema
# ═══════════════════════════════════════════════════════════════

class GetDbSchemaTool(BaseTool):
    name = "get_db_schema"
    description = (
        "Get the structure of database tables. "
        "Call with no arguments to list all tables with their column counts and row counts. "
        "Call with a table_name to get detailed column information for that table."
    )
    parameters = {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "Optional: specific table name to inspect. If empty, returns summary of all tables.",
            },
        },
        "required": [],
    }
    max_retries = 1
    retry_strategy = "exponential"

    async def execute(self, table_name: str = "") -> ToolResult:
        try:
            from db_connector.factory import get_connector
            conn = await get_connector()

            if table_name:
                if not _TABLE_NAME_RE.match(table_name):
                    return ToolResult(success=False, error=f"Invalid table name: {table_name}")
                schema = await conn.get_schema(table_name)
                return ToolResult(success=True, data=schema)

            tables = await conn.get_tables()
            summary = []
            for t in tables:
                try:
                    s = await conn.get_schema(t)
                    summary.append({
                        "table_name": t,
                        "column_count": len(s.get("columns", [])),
                        "columns": s.get("columns", []),
                    })
                except Exception as e:
                    summary.append({"table_name": t, "error": str(e)})
            return ToolResult(success=True, data={"tables": summary, "total_tables": len(tables)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# Tool: query_db
# ═══════════════════════════════════════════════════════════════

class QueryDbTool(BaseTool):
    name = "query_db"
    description = (
        "Execute a read-only SQL SELECT query against the target database. "
        "Results are capped at 500 rows. "
        "Always call get_db_schema first to understand the table structure before writing queries. "
        "Add WHERE clauses to filter results and LIMIT to control row count."
    )
    parameters = {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A complete, executable SELECT SQL statement. Must be read-only.",
            },
        },
        "required": ["sql"],
    }
    max_retries = 1
    retry_strategy = "exponential"

    async def execute(self, sql: str) -> ToolResult:
        if not sql or not sql.strip():
            return ToolResult(success=False, error="SQL query is empty")

        if not _validate_single_statement(sql):
            return ToolResult(success=False, error="Multiple SQL statements are not allowed. Submit each statement separately.")

        if not _is_readonly_sql(sql):
            return ToolResult(
                success=False,
                error="Only read-only SELECT queries are allowed. Use execute_sql for write operations.",
            )

        sql = _add_limit(sql)

        try:
            from db_connector.factory import get_connector
            conn = await get_connector()
            rows = await conn.query(sql)
            total = len(rows)
            truncated = total >= _MAX_ROWS
            result_rows = rows[:_MAX_ROWS]

            columns = list(result_rows[0].keys()) if result_rows else []
            masked_rows = mask_rows(columns, [list(r.values()) for r in result_rows])

            data = {
                "columns": columns,
                "rows": masked_rows,
                "row_count": total,
            }
            if truncated:
                data["truncated"] = True
                data["hint"] = (
                    f"Results truncated at {_MAX_ROWS} rows. "
                    f"Add more specific WHERE conditions to narrow results."
                )

            return ToolResult(success=True, data=data)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# Tool: execute_sql (Phase 2 — full review workflow)
# ═══════════════════════════════════════════════════════════════

class ExecuteSqlTool(BaseTool):
    name = "execute_sql"
    description = (
        "Submit a database write operation (INSERT, UPDATE, DELETE) for review. "
        "The operation will be safety-checked, then submitted to a review queue. "
        "A human must approve it in the database panel before execution. "
        "Dangerous operations (DROP, TRUNCATE, ALTER) are blocked entirely. "
        "DELETE and UPDATE without WHERE are also blocked."
    )
    parameters = {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "The SQL write statement (INSERT/UPDATE/DELETE) to submit for review.",
            },
            "reason": {
                "type": "string",
                "description": "Explanation of why this operation is needed.",
            },
        },
        "required": ["sql", "reason"],
    }
    max_retries = 0
    retry_strategy = "none"

    async def execute(self, sql: str, reason: str) -> ToolResult:
        if not sql or not sql.strip():
            return ToolResult(success=False, error="SQL statement is empty")
        if get_actor_role() == "viewer":
            return ToolResult(
                success=False,
                error="Viewer role cannot submit write operations. Contact an operator or approver.",
            )

        # 1. Safety classification (delegated to SafetyChecker)
        from db_connector.safety import SafetyChecker
        safety = SafetyChecker()
        result = safety.check(sql)

        if result.blocked:
            return ToolResult(success=False, error=result.message, data={
                "sql": sql, "blocked": True, "level": result.level,
                "message": result.message,
            })

        # 2. Parse operation info
        cleaned = _strip_strings(sql).strip().upper()
        if cleaned.startswith("SELECT"):
            return ToolResult(
                success=False,
                error="Use query_db for SELECT queries. execute_sql is for write operations only.",
            )

        first_word = cleaned.split()[0] if cleaned.split() else "UNKNOWN"
        table_name = _extract_table_name(sql, first_word)

        # 3. Estimate affected rows
        from db_connector.factory import get_connector
        conn = await get_connector()
        affected_rows = 0
        try:
            where_clause = _extract_where(sql)
            if where_clause and first_word in ("UPDATE", "DELETE"):
                validate_user_identifier(table_name, "table name")
                quoted_table = conn.quote_identifier(table_name)
                count_rows = await conn.query(
                    f'SELECT COUNT(*) as cnt FROM {quoted_table} WHERE {where_clause}'
                )
                affected_rows = count_rows[0]["cnt"] if count_rows else 0
        except Exception:
            pass

        # 4. Submit to review queue (DB-backed, shared with API layer)
        from api.db_operations import _create_review_task
        task = await _create_review_task(
            sql=sql, reason=reason, operation_type=first_word,
            affected_table=table_name, affected_rows=affected_rows,
            preview_columns=[], preview_rows=[],
            submitted_by=get_actor_id(),
        )
        task_id = task["id"]

        return ToolResult(success=True, data={
            "status": "pending_review",
            "review_id": task_id,
            "sql": sql,
            "reason": reason,
            "affected_table": table_name,
            "affected_rows": affected_rows,
            "safety_level": result.level,
            "message": (
                f"SQL submitted for review (ID: {task_id}). "
                f"Affected: {table_name}, ~{affected_rows} rows. "
                f"Please approve or reject this operation in the database panel."
            ),
        })
