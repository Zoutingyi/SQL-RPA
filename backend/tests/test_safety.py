"""Tests for SafetyChecker — 17 safety rules covering all SQL types."""

import pytest
from db_connector.safety import SafetyChecker

# Keys use uppercase because SafetyChecker._check_full_scan runs
# regex on sql.upper(), capturing table names in uppercase.
MOCK_TABLE_SIZES = {
    "USERS": 1000,
    "ORDERS": 150000,  # > 100k rows to trigger full-scan warnings
    "PRODUCTS": 50,
}


@pytest.fixture
def safety():
    return SafetyChecker()


class TestDropTruncateAlter:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("sql", [
        "DROP TABLE users",
        "DROP TABLE IF EXISTS users",
        "DROP DATABASE main",
    ])
    async def test_reject_drop(self, safety, sql):
        r = safety.check(sql, MOCK_TABLE_SIZES)
        assert r.blocked is True
        assert r.level == "critical"

    @pytest.mark.asyncio
    async def test_reject_truncate(self, safety):
        r = safety.check("TRUNCATE TABLE users", MOCK_TABLE_SIZES)
        assert r.blocked is True
        assert r.level == "critical"

    @pytest.mark.asyncio
    async def test_reject_alter(self, safety):
        r = safety.check("ALTER TABLE users ADD COLUMN phone TEXT", MOCK_TABLE_SIZES)
        assert r.blocked is True
        assert r.level == "critical"


class TestDeleteSafety:
    @pytest.mark.asyncio
    async def test_reject_delete_without_where(self, safety):
        r = safety.check("DELETE FROM users", MOCK_TABLE_SIZES)
        assert r.blocked is True
        assert "WHERE" in r.message.upper()

    @pytest.mark.asyncio
    async def test_reject_delete_where_1_equals_1(self, safety):
        r = safety.check("DELETE FROM users WHERE 1=1", MOCK_TABLE_SIZES)
        assert r.blocked is True

    @pytest.mark.asyncio
    async def test_allow_delete_with_real_where(self, safety):
        r = safety.check("DELETE FROM users WHERE status = 'expired'", MOCK_TABLE_SIZES)
        assert r.allowed is True
        assert r.requires_review is True
        assert r.level == "danger"


class TestUpdateSafety:
    @pytest.mark.asyncio
    async def test_reject_update_without_where(self, safety):
        r = safety.check("UPDATE users SET name = 'x'", MOCK_TABLE_SIZES)
        assert r.blocked is True

    @pytest.mark.asyncio
    async def test_reject_update_where_1_equals_1(self, safety):
        r = safety.check("UPDATE users SET name='x' WHERE 1=1", MOCK_TABLE_SIZES)
        assert r.blocked is True

    @pytest.mark.asyncio
    async def test_allow_update_with_where(self, safety):
        r = safety.check("UPDATE users SET name = 'Bob' WHERE id = 2", MOCK_TABLE_SIZES)
        assert r.allowed is True
        assert r.requires_review is True
        assert r.level == "warning"


class TestInsertSafety:
    @pytest.mark.asyncio
    async def test_allow_insert(self, safety):
        r = safety.check(
            "INSERT INTO users (name, email) VALUES ('New', 'new@test.com')",
            MOCK_TABLE_SIZES,
        )
        assert r.allowed is True
        assert r.requires_review is True
        assert r.level == "info"

    @pytest.mark.asyncio
    async def test_allow_insert_multi_values(self, safety):
        r = safety.check(
            "INSERT INTO users (name) VALUES ('A'), ('B')",
            MOCK_TABLE_SIZES,
        )
        assert r.allowed is True


class TestSelectSafety:
    @pytest.mark.asyncio
    async def test_allow_select(self, safety):
        r = safety.check("SELECT * FROM users WHERE id = 1", MOCK_TABLE_SIZES)
        assert r.allowed is True
        assert r.requires_review is False
        assert r.level == "info"

    @pytest.mark.asyncio
    async def test_warn_full_scan_large_table(self, safety):
        r = safety.check("SELECT * FROM orders", MOCK_TABLE_SIZES)
        assert r.allowed is True
        assert len(r.warnings) >= 1

    @pytest.mark.asyncio
    async def test_no_warn_small_table_full_scan(self, safety):
        r = safety.check("SELECT * FROM products", MOCK_TABLE_SIZES)
        has_fs = any("全表扫描" in w or "Full scan" in w for w in r.warnings)
        assert has_fs is False

    @pytest.mark.asyncio
    async def test_select_with_join(self, safety):
        r = safety.check(
            "SELECT u.name, o.product_name FROM users u "
            "JOIN orders o ON u.id = o.user_id",
            MOCK_TABLE_SIZES,
        )
        assert r.allowed is True


class TestMultiStatement:
    @pytest.mark.asyncio
    async def test_reject_stacked_queries(self, safety):
        r = safety.check("SELECT 1; DROP TABLE users;", MOCK_TABLE_SIZES)
        assert r.blocked is True

    @pytest.mark.asyncio
    async def test_reject_stacked_with_newline(self, safety):
        r = safety.check("SELECT * FROM users;\nDELETE FROM orders;", MOCK_TABLE_SIZES)
        assert r.blocked is True


class TestEmptyAndUnknown:
    @pytest.mark.asyncio
    async def test_reject_empty_sql(self, safety):
        r = safety.check("")
        assert r.blocked is True

    @pytest.mark.asyncio
    async def test_reject_whitespace_only(self, safety):
        r = safety.check("   ")
        assert r.blocked is True

    @pytest.mark.asyncio
    async def test_reject_unknown_type(self, safety):
        r = safety.check("CREATE TABLE foo (id INT)")
        assert r.blocked is True


class TestResultSizeCheck:
    def test_small_result_no_warning(self, safety):
        warnings = safety.check_result_size(100)
        assert warnings == []

    def test_large_result_warns(self, safety):
        warnings = safety.check_result_size(6000)
        assert len(warnings) >= 1

    def test_at_threshold_warns(self, safety):
        warnings = safety.check_result_size(5001)
        assert len(warnings) >= 1
