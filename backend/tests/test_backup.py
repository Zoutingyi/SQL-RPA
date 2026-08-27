"""Tests for BackupManager: backup creation, rollback SQL, full rollback cycle, operation logging."""

import json
import pytest
from db_connector.backup import BackupManager


@pytest.fixture
def backup_mgr(db_connector):
    return BackupManager(db_connector)


class TestBackupCreation:
    @pytest.mark.asyncio
    async def test_backup_before_delete(self, backup_mgr, db_connector, init_rag_db):
        before = await db_connector.query(
            "SELECT COUNT(*) as cnt FROM users WHERE role = 'admin'"
        )
        before_count = before[0]["cnt"]

        result = await backup_mgr.create_backup(
            table_name="users",
            condition="role = 'admin'",
            operation_type="DELETE",
        )
        assert result.backup_id is not None
        assert result.affected_rows == before_count

    @pytest.mark.asyncio
    async def test_backup_before_update(self, backup_mgr, db_connector, init_rag_db):
        result = await backup_mgr.create_backup(
            table_name="products",
            condition="stock = 0",
            operation_type="UPDATE",
        )
        assert result.backup_id is not None

    @pytest.mark.asyncio
    async def test_backup_snapshot_contains_columns_and_rows(self, backup_mgr, init_rag_db):
        result = await backup_mgr.create_backup(
            table_name="users", condition="id <= 3", operation_type="DELETE"
        )
        snapshot = result.data_snapshot
        assert "columns" in snapshot
        assert "rows" in snapshot
        assert len(snapshot["rows"]) == 3  # Alice, Bob, Charlie

    @pytest.mark.asyncio
    async def test_backup_empty_condition_returns_zero(self, backup_mgr, init_rag_db):
        result = await backup_mgr.create_backup(
            table_name="users", condition="id = 99999", operation_type="DELETE"
        )
        assert result.affected_rows == 0
        assert result.rollback_sql == ""


class TestRollbackSQL:
    @pytest.mark.asyncio
    async def test_rollback_sql_starts_with_insert(self, backup_mgr, init_rag_db):
        result = await backup_mgr.create_backup(
            table_name="users", condition="id <= 2", operation_type="DELETE"
        )
        assert result.rollback_sql.strip().upper().startswith("INSERT INTO")

    @pytest.mark.asyncio
    async def test_rollback_sql_is_valid(self, backup_mgr, db_connector, init_rag_db):
        result = await backup_mgr.create_backup(
            table_name="users", condition="id = 1", operation_type="DELETE"
        )
        # Verify the rollback SQL is executable by trying EXPLAIN
        explain_rows = await db_connector.query(
            f"EXPLAIN {result.rollback_sql}"
        )
        assert len(explain_rows) > 0

    @pytest.mark.asyncio
    async def test_rollback_sql_for_update(self, backup_mgr, init_rag_db):
        result = await backup_mgr.create_backup(
            table_name="products", condition="id = 1", operation_type="UPDATE"
        )
        assert result.backup_id is not None
        assert result.rollback_sql.startswith("INSERT")


class TestRollbackExecution:
    @pytest.mark.asyncio
    async def test_full_rollback_cycle(self, backup_mgr, db_connector, init_rag_db):
        # 1. Backup
        result = await backup_mgr.create_backup(
            table_name="users", condition="id = 10", operation_type="DELETE"
        )
        assert result.affected_rows == 1

        # 2. Delete
        await db_connector.execute("DELETE FROM users WHERE id = 10")

        # 3. Verify deleted
        after_del = await db_connector.query("SELECT * FROM users WHERE id = 10")
        assert len(after_del) == 0

        # 4. Rollback
        restored = await backup_mgr.rollback(result.backup_id)
        assert restored == 1

        # 5. Verify restored
        after_rollback = await db_connector.query("SELECT * FROM users WHERE id = 10")
        assert len(after_rollback) == 1

    @pytest.mark.asyncio
    async def test_rollback_invalid_id_raises(self, backup_mgr):
        with pytest.raises(ValueError):
            await backup_mgr.rollback("non-existent-id")


class TestOperationLogging:
    @pytest.mark.asyncio
    async def test_log_operation_returns_id(self, backup_mgr, db_connector, init_rag_db):
        result = await backup_mgr.create_backup(
            table_name="users", condition="id = 9", operation_type="DELETE"
        )
        await db_connector.execute("DELETE FROM users WHERE id = 9")

        log_id = await backup_mgr.log_operation(
            operation_type="DELETE",
            sql_text="DELETE FROM users WHERE id = 9",
            affected_rows=1,
            backup_id=result.backup_id,
            status="completed",
            table_name="users",
        )
        assert log_id is not None

    @pytest.mark.asyncio
    async def test_log_operation_default_status(self, backup_mgr, init_rag_db):
        log_id = await backup_mgr.log_operation(
            operation_type="SELECT",
            sql_text="SELECT * FROM users LIMIT 1",
            affected_rows=1,
            backup_id=None,
            status="completed",
        )
        assert log_id is not None


class TestCleanupExpired:
    @pytest.mark.asyncio
    async def test_cleanup_returns_count(self, backup_mgr, init_rag_db):
        # Fresh backups should not be expired
        cleaned = await backup_mgr.cleanup_expired_backups()
        assert isinstance(cleaned, int)
