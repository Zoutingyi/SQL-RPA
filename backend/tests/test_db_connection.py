"""Tests for database connection verification: error categorization, status, reconnect."""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def client(test_rpa_db, init_rag_db):
    from main import app
    app.state.testing = True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    from db_connector.factory import close_connector
    await close_connector()


class TestErrorCategorization:
    """Unit tests for _categorize_db_error covering all 6 error types."""

    @pytest.fixture
    def categorizer(self):
        from api.db_operations import _categorize_db_error
        return _categorize_db_error

    def test_not_implemented_error(self, categorizer):
        result = categorizer(NotImplementedError("MySQL not implemented"))
        assert result["error_type"] == "not_implemented"
        assert "尚未实现" in result["message"]
        assert "suggestion" in result

    def test_unsupported_type_error(self, categorizer):
        result = categorizer(ValueError("Unsupported database type: postgresql"))
        assert result["error_type"] == "unsupported_type"
        assert "sqlite" in result["suggestion"]

    def test_file_not_found_error(self, categorizer):
        import aiosqlite
        try:
            raise aiosqlite.Error("unable to open database file")
        except aiosqlite.Error as e:
            result = categorizer(e)
        assert result["error_type"] == "file_not_found"
        assert "路径" in result["message"]

    def test_file_not_found_no_such_file(self, categorizer):
        import aiosqlite
        try:
            raise aiosqlite.Error("no such file: /tmp/missing.db")
        except aiosqlite.Error as e:
            result = categorizer(e)
        assert result["error_type"] == "file_not_found"

    def test_permission_denied_error(self, categorizer):
        result = categorizer(PermissionError("permission denied"))
        assert result["error_type"] == "permission_denied"
        assert "suggestion" in result
        assert len(result["message"]) > 0

    def test_connection_failed_error(self, categorizer):
        result = categorizer(ConnectionRefusedError("connection refused"))
        assert result["error_type"] == "connection_failed"

    def test_connection_timeout(self, categorizer):
        result = categorizer(TimeoutError("connection timeout"))
        assert result["error_type"] == "connection_failed"

    def test_auth_failed_error(self, categorizer):
        result = categorizer(OSError("auth failed: bad password"))
        assert result["error_type"] == "auth_failed"
        assert "密码" in result["suggestion"]

    def test_unknown_error(self, categorizer):
        result = categorizer(RuntimeError("something bizarre happened"))
        assert result["error_type"] == "unknown"
        assert len(result["message"]) > 0


class TestStatusEndpoint:
    """Integration tests for GET /api/db_operations/status."""

    @pytest.mark.asyncio
    async def test_status_connected(self, client):
        res = await client.get("/api/db_operations/status")
        assert res.status_code == 200
        data = res.json()
        assert data["connected"] is True
        assert data["db_type"] == "sqlite"
        assert data["table_count"] >= 3
        assert "error" not in data

    @pytest.mark.asyncio
    async def test_status_returns_error_for_mysql(self, client):
        """When db_type is mysql but server is unreachable, status returns error."""
        from config import settings
        from db_connector.factory import close_connector

        original_type = settings.db_type
        original_host = settings.db_host
        try:
            await close_connector()
            settings.db_type = "mysql"
            settings.db_host = "127.0.0.1"  # non-existent server

            res = await client.get("/api/db_operations/status")
            assert res.status_code == 200
            data = res.json()
            assert data["connected"] is False
            assert data["db_type"] == "mysql"
            assert "error" in data
            assert data["error"]["error_type"] == "connection_failed"
            assert "suggestion" in data["error"]
        finally:
            settings.db_type = original_type
            settings.db_host = original_host
            await close_connector()

    @pytest.mark.asyncio
    async def test_status_returns_error_for_bad_path(self, client, tmp_path):
        """When sqlite path points to a directory (not a file), status returns error."""
        from config import settings
        from db_connector.factory import close_connector

        original_path = settings.db_sqlite_path
        try:
            await close_connector()
            settings.db_type = "sqlite"
            # Point to an existing directory — aiosqlite will fail to open it as a DB
            settings.db_sqlite_path = str(tmp_path)

            res = await client.get("/api/db_operations/status")
            assert res.status_code == 200
            data = res.json()
            assert data["connected"] is False
            assert "error" in data
            assert data["error"]["error_type"] in ("file_not_found", "unknown")
        finally:
            settings.db_sqlite_path = original_path
            settings.db_type = "sqlite"
            await close_connector()

    @pytest.mark.asyncio
    async def test_status_reports_missing_postgresql_driver(self, client):
        """PostgreSQL is supported and reports a missing optional driver clearly."""
        from config import settings
        from db_connector.factory import close_connector

        original_type = settings.db_type
        try:
            await close_connector()
            settings.db_type = "postgresql"

            res = await client.get("/api/db_operations/status")
            assert res.status_code == 200
            data = res.json()
            assert data["connected"] is False
            assert data["error"]["error_type"] in {"dependency_missing", "connection_failed"}
        finally:
            settings.db_type = original_type
            await close_connector()


class TestReconnectEndpoint:
    """Integration tests for POST /api/db_operations/reconnect."""

    @pytest.mark.asyncio
    async def test_reconnect_succeeds_when_healthy(self, client):
        res = await client.post("/api/db_operations/reconnect")
        assert res.status_code == 200
        data = res.json()
        assert data["connected"] is True
        assert data["db_type"] == "sqlite"
        assert data["table_count"] >= 3

    @pytest.mark.asyncio
    async def test_reconnect_returns_error_when_broken(self, client):
        """Reconnect with unreachable MySQL returns error info."""
        from config import settings
        from db_connector.factory import close_connector

        original_type = settings.db_type
        original_host = settings.db_host
        try:
            await close_connector()
            settings.db_type = "mysql"
            settings.db_host = "127.0.0.1"

            res = await client.post("/api/db_operations/reconnect")
            assert res.status_code == 200
            data = res.json()
            assert data["connected"] is False
            assert data["error"]["error_type"] == "connection_failed"
        finally:
            settings.db_type = original_type
            settings.db_host = original_host
            await close_connector()
            # Restore working connection for subsequent tests
            settings.db_type = "sqlite"
            from db_connector.factory import get_connector
            await get_connector()

    @pytest.mark.asyncio
    async def test_reconnect_recovery_flow(self, client):
        """Break connection → verify error → fix config → reconnect → verify success."""
        from config import settings
        from db_connector.factory import close_connector

        original_type = settings.db_type
        original_path = settings.db_sqlite_path
        original_host = settings.db_host
        try:
            # Step 1: Break the connection by pointing to unreachable MySQL
            await close_connector()
            settings.db_type = "mysql"
            settings.db_host = "127.0.0.1"

            res1 = await client.get("/api/db_operations/status")
            assert res1.json()["connected"] is False
            assert res1.json()["error"]["error_type"] == "connection_failed"

            # Step 2: Fix config and reconnect
            settings.db_type = original_type
            settings.db_sqlite_path = original_path
            settings.db_host = original_host

            res2 = await client.post("/api/db_operations/reconnect")
            assert res2.status_code == 200
            assert res2.json()["connected"] is True

            # Step 3: Verify status is now healthy
            res3 = await client.get("/api/db_operations/status")
            assert res3.json()["connected"] is True
            assert res3.json()["table_count"] >= 3
        finally:
            settings.db_type = original_type
            settings.db_sqlite_path = original_path
            settings.db_host = original_host
            await close_connector()
            from db_connector.factory import get_connector
            await get_connector()


class TestReconnectIdempotency:
    """Reconnect should work even when called multiple times."""

    @pytest.mark.asyncio
    async def test_reconnect_twice(self, client):
        r1 = await client.post("/api/db_operations/reconnect")
        assert r1.json()["connected"] is True

        r2 = await client.post("/api/db_operations/reconnect")
        assert r2.json()["connected"] is True

    @pytest.mark.asyncio
    async def test_status_after_multiple_reconnects(self, client):
        for _ in range(3):
            await client.post("/api/db_operations/reconnect")

        res = await client.get("/api/db_operations/status")
        assert res.json()["connected"] is True
        assert res.json()["table_count"] >= 3
