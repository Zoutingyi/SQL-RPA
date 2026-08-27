import aiomysql
import time
from contextlib import asynccontextmanager

from .base import DatabaseConnector
from .sql_utils import quote_identifier, validate_user_identifier


class _MysqlTransaction:
    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql: str, params: dict | None = None) -> int:
        async with self._conn.cursor() as cur:
            return await cur.execute(sql, params or {})

    async def execute_many(self, sql: str, rows: list[tuple]) -> int:
        async with self._conn.cursor() as cur:
            return await cur.executemany(sql, rows)

    async def query(self, sql: str, params=None) -> list[dict]:
        async with self._conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params or ())
            return await cur.fetchall()


class MySQLConnector(DatabaseConnector):
    """MySQL database connector using aiomysql."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str, pool_size: int = 5):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._pool_size = pool_size
        self._pool: aiomysql.Pool | None = None
        self._tables_cache: list[str] | None = None
        self._tables_cache_at: float = 0.0
        self._tables_ttl = 30.0

    async def connect(self) -> None:
        self._pool = await aiomysql.create_pool(
            host=self._host, port=self._port,
            user=self._user, password=self._password,
            db=self._database, minsize=1, maxsize=self._pool_size,
            autocommit=True, charset='utf8mb4',
        )
        self._invalidate_table_cache()

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
        self._invalidate_table_cache()

    async def health_check(self) -> bool:
        try:
            if self._pool is None:
                return False
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    return True
        except Exception:
            return False

    async def _execute_with_conn(self, sql: str, params: dict | None = None) -> list[dict]:
        started = time.monotonic()
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params or {})
                if cur.description:
                    rows = await cur.fetchall()
                    from observability import observe
                    elapsed = (time.monotonic() - started) * 1000
                    observe("db", "query", elapsed)
                    from config import settings
                    if elapsed >= settings.slow_query_ms:
                        import logging
                        logging.getLogger("sql_rpa").warning("Slow MySQL query elapsed_ms=%.1f", elapsed)
                    return rows
                return []

    async def _execute_write(self, sql: str, params: dict | None = None) -> int:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                affected = await cur.execute(sql, params or {})
                return affected

    async def get_tables(self) -> list[str]:
        now = time.monotonic()
        if self._tables_cache is not None and now - self._tables_cache_at < self._tables_ttl:
            return list(self._tables_cache)
        rows = await self._execute_with_conn("SHOW TABLES")
        key = list(rows[0].keys())[0] if rows else ""
        self._tables_cache = [r[key] for r in rows] if key else []
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
        rows = await self._execute_with_conn(
            "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS type, "
            "IS_NULLABLE AS nullable, COLUMN_KEY AS `key`, COLUMN_DEFAULT AS `default` "
            "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = %s AND TABLE_SCHEMA = %s "
            "ORDER BY ORDINAL_POSITION",
            (table_name, self._database),
        )
        columns = []
        for r in rows:
            columns.append({
                "name": r["name"],
                "type": r["type"],
                "nullable": r["nullable"] == "YES",
                "key": "PRI" if r["key"] == "PRI" else "",
                "default": r["default"],
            })
        return {"table_name": table_name, "columns": columns}

    async def query(self, sql: str, params: dict | None = None) -> list[dict]:
        return await self._execute_with_conn(sql, params)

    async def execute(self, sql: str, params: dict | None = None) -> int:
        return await self._execute_write(sql, params)

    async def execute_many(self, sql: str, rows: list[tuple]) -> int:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                return await cur.executemany(sql, rows)

    def quote_identifier(self, identifier: str) -> str:
        return quote_identifier(identifier, "mysql")

    def placeholder(self) -> str:
        return "%s"

    @asynccontextmanager
    async def transaction(self):
        async with self._pool.acquire() as conn:
            await conn.autocommit(False)
            try:
                yield _MysqlTransaction(conn)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            finally:
                await conn.autocommit(True)
