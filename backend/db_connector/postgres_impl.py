import re
import time
from contextlib import asynccontextmanager

import asyncpg

from .base import DatabaseConnector
from .sql_utils import quote_identifier, validate_user_identifier


def _normalize(sql: str, params=None):
    if not params:
        return sql, ()
    if isinstance(params, dict):
        values = []
        def replace(match):
            values.append(params[match.group(1)])
            return f"${len(values)}"
        return re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", replace, sql), tuple(values)
    return sql, tuple(params)


class _PostgresTransaction:
    def __init__(self, conn):
        self._conn = conn

    async def query(self, sql: str, params=None) -> list[dict]:
        sql, values = _normalize(sql, params)
        return [dict(row) for row in await self._conn.fetch(sql, *values)]

    async def execute(self, sql: str, params=None) -> int:
        sql, values = _normalize(sql, params)
        status = await self._conn.execute(sql, *values)
        return int(status.rsplit(" ", 1)[-1]) if status.rsplit(" ", 1)[-1].isdigit() else 0

    async def execute_many(self, sql: str, rows: list[tuple]) -> int:
        await self._conn.executemany(sql, rows)
        return len(rows)


class PostgreSQLConnector(DatabaseConnector):
    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str, pool_size: int = 5):
        self._config = dict(host=host, port=port, user=user, password=password, database=database)
        self._pool_size = pool_size
        self._pool = None
        self._tables_cache = None
        self._tables_cache_at = 0.0

    async def connect(self):
        self._pool = await asyncpg.create_pool(**self._config, min_size=1, max_size=self._pool_size)

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def health_check(self):
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                return await conn.fetchval("SELECT 1") == 1
        except Exception:
            return False

    async def query(self, sql: str, params=None):
        started = time.monotonic()
        sql, values = _normalize(sql, params)
        async with self._pool.acquire() as conn:
            rows = [dict(row) for row in await conn.fetch(sql, *values)]
        from observability import observe
        observe("db", "query", (time.monotonic() - started) * 1000)
        return rows

    async def execute(self, sql: str, params=None):
        sql, values = _normalize(sql, params)
        async with self._pool.acquire() as conn:
            status = await conn.execute(sql, *values)
        tail = status.rsplit(" ", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    async def execute_many(self, sql: str, rows: list[tuple]):
        async with self._pool.acquire() as conn:
            await conn.executemany(sql, rows)
        return len(rows)

    async def get_tables(self):
        if self._tables_cache is not None and time.monotonic() - self._tables_cache_at < 30:
            return list(self._tables_cache)
        rows = await self.query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        )
        self._tables_cache = [row["table_name"] for row in rows]
        self._tables_cache_at = time.monotonic()
        return list(self._tables_cache)

    async def get_schema(self, table_name: str):
        try:
            validate_user_identifier(table_name, "table name")
        except ValueError:
            return {"table_name": table_name, "columns": []}
        if table_name not in await self.get_tables():
            return {"table_name": table_name, "columns": []}
        rows = await self.query(
            "SELECT c.column_name AS name, c.data_type AS type, "
            "(c.is_nullable='YES') AS nullable, "
            "CASE WHEN tc.constraint_type='PRIMARY KEY' THEN 'PRI' ELSE '' END AS key, "
            "c.column_default AS default FROM information_schema.columns c "
            "LEFT JOIN information_schema.key_column_usage kcu ON "
            "kcu.table_schema=c.table_schema AND kcu.table_name=c.table_name AND kcu.column_name=c.column_name "
            "LEFT JOIN information_schema.table_constraints tc ON tc.constraint_name=kcu.constraint_name "
            "AND tc.table_schema=kcu.table_schema WHERE c.table_schema='public' AND c.table_name=$1 "
            "ORDER BY c.ordinal_position", (table_name,)
        )
        return {"table_name": table_name, "columns": rows}

    def quote_identifier(self, identifier: str):
        return quote_identifier(identifier, "postgres")

    def placeholder(self):
        return "$1"

    @asynccontextmanager
    async def transaction(self):
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield _PostgresTransaction(conn)
