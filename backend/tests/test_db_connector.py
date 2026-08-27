"""Tests for SqliteConnector: connect/close/query/execute/schema/health_check."""

import pytest
from db_connector.sqlite_impl import SqliteConnector


class TestConnectLifecycle:
    @pytest.mark.asyncio
    async def test_connect_success(self, db_connector):
        assert db_connector._conn is not None

    @pytest.mark.asyncio
    async def test_close(self, db_connector):
        await db_connector.close()
        assert db_connector._conn is None

    @pytest.mark.asyncio
    async def test_health_check_connected(self, db_connector):
        ok = await db_connector.health_check()
        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_after_close(self, db_connector):
        await db_connector.close()
        ok = await db_connector.health_check()
        assert ok is False

    @pytest.mark.asyncio
    async def test_connect_creates_new_db(self):
        """SQLiteConnector creates parent dirs and DB file for new paths."""
        import tempfile, os
        tmp = os.path.join(tempfile.gettempdir(), "test_conn_new", "db.sqlite")
        conn = SqliteConnector(tmp)
        try:
            await conn.connect()
            assert conn._conn is not None
        finally:
            await conn.close()
            try:
                os.remove(tmp)
                os.rmdir(os.path.dirname(tmp))
            except OSError:
                pass


class TestQuery:
    @pytest.mark.asyncio
    async def test_select_all_users(self, db_connector):
        rows = await db_connector.query("SELECT * FROM users")
        assert len(rows) == 10
        assert rows[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_select_with_param(self, db_connector):
        rows = await db_connector.query(
            "SELECT * FROM users WHERE role = :role", {"role": "admin"}
        )
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_select_with_limit(self, db_connector):
        rows = await db_connector.query("SELECT * FROM orders LIMIT 3")
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_select_count(self, db_connector):
        rows = await db_connector.query("SELECT COUNT(*) as cnt FROM products")
        assert rows[0]["cnt"] == 8

    @pytest.mark.asyncio
    async def test_select_with_join(self, db_connector):
        rows = await db_connector.query(
            "SELECT u.name, o.product_name FROM users u "
            "JOIN orders o ON u.id = o.user_id LIMIT 5"
        )
        assert len(rows) >= 1
        assert "name" in rows[0]
        assert "product_name" in rows[0]

    @pytest.mark.asyncio
    async def test_select_null_value(self, db_connector):
        rows = await db_connector.query("SELECT email FROM users WHERE id = 6")
        assert rows[0]["email"] is None  # Frank's email

    @pytest.mark.asyncio
    async def test_select_empty_result(self, db_connector):
        rows = await db_connector.query("SELECT * FROM users WHERE id = 99999")
        assert rows == []

    @pytest.mark.asyncio
    async def test_select_with_order(self, db_connector):
        rows = await db_connector.query("SELECT id FROM users ORDER BY id ASC")
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids)


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_insert(self, db_connector):
        rc = await db_connector.execute(
            "INSERT INTO users (name, email, age) VALUES (:name, :email, :age)",
            {"name": "TestUser", "email": "t@t.com", "age": 99},
        )
        assert rc == 1
        rows = await db_connector.query("SELECT * FROM users WHERE name = 'TestUser'")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_execute_update(self, db_connector):
        await db_connector.execute(
            "UPDATE users SET age = :age WHERE id = :id", {"age": 100, "id": 1}
        )
        rows = await db_connector.query("SELECT age FROM users WHERE id = 1")
        assert rows[0]["age"] == 100

    @pytest.mark.asyncio
    async def test_execute_delete(self, db_connector):
        await db_connector.execute("DELETE FROM users WHERE id = 10")
        rows = await db_connector.query("SELECT * FROM users WHERE id = 10")
        assert rows == []

    @pytest.mark.asyncio
    async def test_execute_invalid_sql(self, db_connector):
        with pytest.raises(Exception):
            await db_connector.execute("NOT_A_VALID_STATEMENT")


class TestSchema:
    @pytest.mark.asyncio
    async def test_get_tables(self, db_connector):
        tables = await db_connector.get_tables()
        assert "users" in tables
        assert "orders" in tables
        assert "products" in tables

    @pytest.mark.asyncio
    async def test_get_schema_users(self, db_connector):
        schema = await db_connector.get_schema("users")
        assert schema["table_name"] == "users"
        col_names = [c["name"] for c in schema["columns"]]
        assert "id" in col_names
        assert "name" in col_names
        assert "email" in col_names

    @pytest.mark.asyncio
    async def test_get_schema_primary_key(self, db_connector):
        schema = await db_connector.get_schema("users")
        pk_cols = [c for c in schema["columns"] if c["key"] == "PRI"]
        assert len(pk_cols) == 1
        assert pk_cols[0]["name"] == "id"

    @pytest.mark.asyncio
    async def test_get_schema_nullable(self, db_connector):
        schema = await db_connector.get_schema("users")
        email_col = next(c for c in schema["columns"] if c["name"] == "email")
        assert email_col["nullable"] is True

    @pytest.mark.asyncio
    async def test_get_schema_nonexistent_table(self, db_connector):
        """PRAGMA returns empty columns for nonexistent tables, no exception."""
        schema = await db_connector.get_schema("nonexistent_table")
        assert schema["table_name"] == "nonexistent_table"
        assert schema["columns"] == []


class TestQueryResultFormat:
    @pytest.mark.asyncio
    async def test_result_is_list_of_dicts(self, db_connector):
        rows = await db_connector.query("SELECT id, name FROM users LIMIT 1")
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)
        assert set(rows[0].keys()) == {"id", "name"}

    @pytest.mark.asyncio
    async def test_result_null_handling(self, db_connector):
        rows = await db_connector.query("SELECT email FROM users WHERE id = 6")
        assert rows[0]["email"] is None

    @pytest.mark.asyncio
    async def test_result_int_type(self, db_connector):
        rows = await db_connector.query("SELECT id, age FROM users WHERE id = 1")
        assert isinstance(rows[0]["id"], int)
        assert isinstance(rows[0]["age"], int)
