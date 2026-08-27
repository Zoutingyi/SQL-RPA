import aiosqlite
import time
from pathlib import Path
from contextlib import asynccontextmanager

from .base import DatabaseConnector
from .sql_utils import quote_identifier, validate_user_identifier


class _SqliteTransaction:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def execute(self, sql: str, params: dict | None = None) -> int:
        cursor = await self._conn.execute(sql, params or {})
        return cursor.rowcount

    async def execute_many(self, sql: str, rows: list[tuple]) -> int:
        cursor = await self._conn.executemany(sql, rows)
        return cursor.rowcount

    async def query(self, sql: str, params=None) -> list[dict]:
        cursor = await self._conn.execute(sql, params or {})
        return [dict(row) for row in await cursor.fetchall()]


class SqliteConnector(DatabaseConnector):
    """SQLite database connector using aiosqlite."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._tables_cache: list[str] | None = None
        self._tables_cache_at: float = 0.0
        self._tables_ttl = 30.0

    async def connect(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        self._invalidate_table_cache()
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
        self._invalidate_table_cache()

    async def health_check(self) -> bool:
        try:
            if self._conn is None:
                return False
            await self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def get_tables(self) -> list[str]:
        now = time.monotonic()
        if self._tables_cache is not None and now - self._tables_cache_at < self._tables_ttl:
            return list(self._tables_cache)
        cursor = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        rows = await cursor.fetchall()
        self._tables_cache = [r[0] for r in rows]
        self._tables_cache_at = now
        return list(self._tables_cache)

    def _invalidate_table_cache(self) -> None:
        self._tables_cache = None
        self._tables_cache_at = 0.0

    async def get_schema(self, table_name: str) -> dict:
        try:
            validate_user_identifier(table_name, "table name")
        except ValueError:
            return {"table_name": table_name, "columns": []}

        if table_name not in await self.get_tables():
            return {"table_name": table_name, "columns": []}
        quoted = quote_identifier(table_name, "sqlite")
        cursor = await self._conn.execute(f"PRAGMA table_info({quoted})")
        rows = await cursor.fetchall()
        columns = []
        for r in rows:
            columns.append({
                "name": r["name"],
                "type": r["type"],
                "nullable": not r["notnull"],
                "key": "PRI" if r["pk"] else "",
                "default": r["dflt_value"],
            })
        return {"table_name": table_name, "columns": columns}

    async def query(self, sql: str, params: dict | None = None) -> list[dict]:
        started = time.monotonic()
        if params:
            # Convert named params (:param) to sqlite ? placeholders
            named_params = {}
            for key, value in params.items():
                named_params[key] = value
            cursor = await self._conn.execute(sql, named_params)
        else:
            cursor = await self._conn.execute(sql)
        rows = await cursor.fetchall()
        result = [dict(r) for r in rows]
        from observability import observe
        elapsed = (time.monotonic() - started) * 1000
        observe("db", "query", elapsed)
        from config import settings
        if elapsed >= settings.slow_query_ms:
            import logging
            logging.getLogger("sql_rpa").warning("Slow SQLite query elapsed_ms=%.1f", elapsed)
        return result

    async def execute(self, sql: str, params: dict | None = None) -> int:
        if params:
            cursor = await self._conn.execute(sql, params)
        else:
            cursor = await self._conn.execute(sql)
        await self._conn.commit()
        return cursor.rowcount

    async def execute_many(self, sql: str, rows: list[tuple]) -> int:
        cursor = await self._conn.executemany(sql, rows)
        await self._conn.commit()
        return cursor.rowcount

    def quote_identifier(self, identifier: str) -> str:
        return quote_identifier(identifier, "sqlite")

    def placeholder(self) -> str:
        return "?"

    @asynccontextmanager
    async def transaction(self):
        # IMMEDIATE obtains the write reservation before the snapshot read.
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield _SqliteTransaction(self._conn)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
