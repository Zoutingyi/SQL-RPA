"""Integration tests for /api/db_operations endpoints using httpx.AsyncClient."""

import pytest
import pytest_asyncio
import asyncio
from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def client(test_rpa_db, init_rag_db):
    from main import app
    # Disable auth for tests
    app.state.testing = True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    from db_connector.factory import close_connector
    await close_connector()


class TestStatusAndTables:
    @pytest.mark.asyncio
    async def test_get_status(self, client):
        res = await client.get("/api/db_operations/status")
        assert res.status_code == 200
        data = res.json()
        assert data["connected"] is True
        assert data["db_type"] == "sqlite"

    @pytest.mark.asyncio
    async def test_list_tables(self, client):
        res = await client.get("/api/db_operations/tables")
        assert res.status_code == 200
        data = res.json()
        tables = [t["name"] for t in data]
        assert "users" in tables
        assert "orders" in tables
        assert "products" in tables
        assert all("columns" in t and "row_count" in t for t in data)

    @pytest.mark.asyncio
    async def test_get_table_schema(self, client):
        res = await client.get("/api/db_operations/tables/users")
        assert res.status_code == 200
        data = res.json()
        assert data["table_name"] == "users"
        assert "columns" in data
        assert data["row_count"] == 10

    @pytest.mark.asyncio
    async def test_get_table_not_found(self, client):
        res = await client.get("/api/db_operations/tables/nonexistent")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_get_table_data_paginated(self, client):
        res = await client.get(
            "/api/db_operations/tables/users/data?page=1&page_size=3&sort=id&order=asc"
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["rows"]) == 3
        assert data["total"] == 10
        assert "columns" in data

    @pytest.mark.asyncio
    async def test_get_table_data_invalid_sort(self, client):
        res = await client.get(
            "/api/db_operations/tables/users/data?sort=evil;DROP&order=asc"
        )
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_get_table_data_invalid_table_name(self, client):
        res = await client.get("/api/db_operations/tables/evil;DROP/data")
        assert res.status_code == 400


class TestPreviewAPI:
    @pytest.mark.asyncio
    async def test_preview_select(self, client):
        res = await client.post("/api/db_operations/preview", json={
            "sql": "SELECT * FROM users LIMIT 5"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["operation_type"] == "SELECT"
        assert len(data["preview_rows"]) <= 10
        assert len(data["columns"]) >= 5

    @pytest.mark.asyncio
    async def test_preview_select_auto_limit(self, client):
        res = await client.post("/api/db_operations/preview", json={
            "sql": "SELECT * FROM users"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["affected_rows"] <= 500  # auto-limited

    @pytest.mark.asyncio
    async def test_preview_delete_returns_affected_count(self, client):
        res = await client.post("/api/db_operations/preview", json={
            "sql": "DELETE FROM users WHERE role = 'admin'"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["operation_type"] == "DELETE"
        assert data["affected_rows"] == 2

    @pytest.mark.asyncio
    async def test_preview_drop_blocked(self, client):
        res = await client.post("/api/db_operations/preview", json={
            "sql": "DROP TABLE users"
        })
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_delete_without_where_blocked(self, client):
        res = await client.post("/api/db_operations/preview", json={
            "sql": "DELETE FROM users"
        })
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_invalid_sql(self, client):
        res = await client.post("/api/db_operations/preview", json={
            "sql": "NOT VALID SQL"
        })
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_empty_sql(self, client):
        res = await client.post("/api/db_operations/preview", json={"sql": ""})
        assert res.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_preview_insert(self, client):
        res = await client.post("/api/db_operations/preview", json={
            "sql": "INSERT INTO users (name, email) VALUES ('Test', 't@test.com')"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["operation_type"] == "INSERT"
        # INSERT doesn't pre-count affected rows; has_backup depends on affected_rows > 0


class TestSubmitReviewAPI:
    @pytest.mark.asyncio
    async def test_submit_review_update(self, client):
        res = await client.post("/api/db_operations/submit-review", json={
            "sql": "UPDATE users SET age = 30 WHERE id = 1",
            "reason": "Test update",
        })
        assert res.status_code == 200
        data = res.json()
        assert "id" in data
        assert data["status"] == "awaiting_review"

    @pytest.mark.asyncio
    async def test_submit_review_delete_with_reason(self, client):
        res = await client.post("/api/db_operations/submit-review", json={
            "sql": "DELETE FROM users WHERE id = 5",
            "reason": "Test delete",
        })
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_submit_review_insert(self, client):
        res = await client.post("/api/db_operations/submit-review", json={
            "sql": "INSERT INTO users (name, email) VALUES ('SubmitTest', 'st@test.com')",
            "reason": "Test insert",
        })
        assert res.status_code == 200
        assert res.json()["status"] == "awaiting_review"

    @pytest.mark.asyncio
    async def test_submit_review_dangerous_blocked(self, client):
        res = await client.post("/api/db_operations/submit-review", json={
            "sql": "DROP TABLE users",
            "reason": "Bad",
        })
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_submit_review_select_rejected(self, client):
        res = await client.post("/api/db_operations/submit-review", json={
            "sql": "SELECT * FROM users",
            "reason": "Wrong type",
        })
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_concurrent_idempotent_submit_returns_one_review(self, client):
        import uuid
        from models.database import async_session
        from models.schemas import DbReviewTask
        from sqlalchemy import func, select

        key = f"concurrent-{uuid.uuid4()}"
        payload = {"sql": "UPDATE users SET age = 41 WHERE id = 3", "reason": "idempotent"}
        first, second = await asyncio.gather(
            client.post("/api/db_operations/submit-review", headers={"Idempotency-Key": key}, json=payload),
            client.post("/api/db_operations/submit-review", headers={"Idempotency-Key": key}, json=payload),
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        async with async_session() as session:
            count = await session.scalar(
                select(func.count()).select_from(DbReviewTask).where(DbReviewTask.idempotency_key == key)
            )
        assert count == 1


class TestReviewDetailAPI:
    @pytest.mark.asyncio
    async def test_get_review_detail(self, client):
        submit = await client.post("/api/db_operations/submit-review", json={
            "sql": "UPDATE products SET price = 99 WHERE id = 1",
            "reason": "Price adjustment",
        })
        review_id = submit.json()["id"]

        res = await client.get(f"/api/db_operations/review/{review_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["sql"] == "UPDATE products SET price = 99 WHERE id = 1"
        assert "columns" in data
        assert "safety_message" in data
        assert data["columns"] == data.get("preview_columns")

    @pytest.mark.asyncio
    async def test_get_nonexistent_review(self, client):
        res = await client.get("/api/db_operations/review/non-existent-id")
        assert res.status_code == 404


class TestApproveRejectAPI:
    @pytest.mark.asyncio
    async def test_approve_executes_insert(self, client):
        submit = await client.post("/api/db_operations/submit-review", json={
            "sql": "INSERT INTO users (name, email, age) VALUES ('ApproveTest', 'at@test.com', 25)",
            "reason": "Approve test",
        })
        review_id = submit.json()["id"]

        approve = await client.post(
            f"/api/db_operations/review/{review_id}/approve",
            json={"reason": "Confirmed"},
        )
        assert approve.status_code == 200
        assert approve.json()["status"] == "completed"

    @pytest.mark.asyncio
    async def test_reject_does_not_execute(self, client):
        # Count users before
        before = await client.post("/api/db_operations/preview", json={
            "sql": "SELECT COUNT(*) as cnt FROM users"
        })
        before_count = before.json()["preview_rows"][0][0]

        submit = await client.post("/api/db_operations/submit-review", json={
            "sql": "DELETE FROM users WHERE id = 1",
            "reason": "Reject test",
        })
        review_id = submit.json()["id"]

        reject = await client.post(
            f"/api/db_operations/review/{review_id}/reject",
        )
        assert reject.status_code == 200
        assert reject.json()["status"] == "rejected"

        # Verify row count unchanged
        after = await client.post("/api/db_operations/preview", json={
            "sql": "SELECT COUNT(*) as cnt FROM users"
        })
        after_count = after.json()["preview_rows"][0][0]
        assert after_count == before_count

    @pytest.mark.asyncio
    async def test_cannot_approve_twice(self, client):
        submit = await client.post("/api/db_operations/submit-review", json={
            "sql": "INSERT INTO users (name, email) VALUES ('Dup', 'dup@test.com')",
            "reason": "Duplicate approve test",
        })
        review_id = submit.json()["id"]
        await client.post(
            f"/api/db_operations/review/{review_id}/approve",
            json={"reason": "First"},
        )
        res2 = await client.post(
            f"/api/db_operations/review/{review_id}/approve",
            json={"reason": "Second"},
        )
        assert res2.status_code in (400, 409)

    @pytest.mark.asyncio
    async def test_cannot_reject_completed(self, client):
        submit = await client.post("/api/db_operations/submit-review", json={
            "sql": "INSERT INTO users (name, email) VALUES ('RejComp', 'rc@test.com')",
            "reason": "Reject completed test",
        })
        review_id = submit.json()["id"]
        await client.post(
            f"/api/db_operations/review/{review_id}/approve",
            json={"reason": "Approved"},
        )
        res = await client.post(
            f"/api/db_operations/review/{review_id}/reject",
        )
        assert res.status_code == 409

    @pytest.mark.asyncio
    async def test_post_commit_log_failure_is_repaired_without_sql_replay(
        self, client, monkeypatch,
    ):
        import api.db_operations as api
        import uuid

        original_factory = api._get_backup_mgr
        real_manager = original_factory()

        class FailCompletedLogOnce:
            async def create_backup(self, *args, **kwargs):
                return await real_manager.create_backup(*args, **kwargs)

            async def log_operation(self, *args, **kwargs):
                if kwargs.get("review_id"):
                    raise RuntimeError("simulated internal audit outage")
                return await real_manager.log_operation(*args, **kwargs)

        monkeypatch.setattr(api, "_get_backup_mgr", lambda: FailCompletedLogOnce())
        submit = await client.post(
            "/api/db_operations/submit-review",
            headers={"Idempotency-Key": f"post-commit-repair-{uuid.uuid4()}"},
            json={"sql": "UPDATE users SET age = age + 1 WHERE id = 2", "reason": "saga"},
        )
        review_id = submit.json()["id"]
        approve = await client.post(f"/api/db_operations/review/{review_id}/approve", json={})
        assert approve.status_code == 200
        assert approve.json()["status"] == "executed_record_pending"

        from db_connector.factory import get_connector
        connector = await get_connector()
        bob = (await connector.query("SELECT age FROM users WHERE id = 2"))[0]
        assert bob["age"] == 36

        monkeypatch.setattr(api, "_get_backup_mgr", original_factory)
        recover = await client.post(f"/api/db_operations/audit/recover/{review_id}")
        assert recover.status_code == 200
        assert recover.json()["replayed"] is False

        duplicate = await client.post(f"/api/db_operations/review/{review_id}/approve", json={})
        assert duplicate.status_code == 409
        bob = (await connector.query("SELECT age FROM users WHERE id = 2"))[0]
        assert bob["age"] == 36


class TestRollbackAPI:
    @pytest.mark.asyncio
    async def test_rollback_after_delete(self, client):
        # 1. Submit DELETE for review
        submit = await client.post("/api/db_operations/submit-review", json={
            "sql": "DELETE FROM users WHERE id = 7",
            "reason": "Rollback test",
        })
        review_id = submit.json()["id"]

        # 2. Approve
        approve = await client.post(
            f"/api/db_operations/review/{review_id}/approve",
            json={"reason": "Confirmed"},
        )
        assert approve.status_code == 200
        backup_id = approve.json().get("backup_id")

        # 3. Verify deleted
        verify = await client.post("/api/db_operations/preview", json={
            "sql": "SELECT * FROM users WHERE id = 7"
        })
        assert verify.json()["affected_rows"] == 0

        # 4. Rollback
        if backup_id:
            roll = await client.post(
                f"/api/db_operations/rollback/{backup_id}",
                json={"confirm": True, "reason": "rollback test"},
            )
            assert roll.status_code == 200

            # 5. Verify restored
            verify2 = await client.post("/api/db_operations/preview", json={
                "sql": "SELECT * FROM users WHERE id = 7"
            })
            assert verify2.json()["affected_rows"] == 1

    @pytest.mark.asyncio
    async def test_rollback_invalid_id(self, client):
        res = await client.post(
            "/api/db_operations/rollback/nonexistent",
            json={"confirm": True},
        )
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_reverse_backup_failure_aborts_rollback(self, client, monkeypatch):
        import api.db_operations as api
        from models.database import async_session
        from models.schemas import BackupStatus, DbBackup

        submit = await client.post("/api/db_operations/submit-review", json={
            "sql": "DELETE FROM users WHERE id = 8", "reason": "abort rollback test",
        })
        approve = await client.post(
            f"/api/db_operations/review/{submit.json()['id']}/approve", json={}
        )
        backup_id = approve.json()["backup_id"]
        rollback_called = False

        class FailedReverseBackupManager:
            async def create_backup(self, *args, **kwargs):
                raise RuntimeError("simulated reverse backup failure")

            async def rollback(self, *args, **kwargs):
                nonlocal rollback_called
                rollback_called = True
                raise AssertionError("rollback must not be called")

        monkeypatch.setattr(api, "_get_backup_mgr", lambda: FailedReverseBackupManager())
        response = await client.post(
            f"/api/db_operations/rollback/{backup_id}", json={"confirm": True}
        )
        assert response.status_code == 500
        assert rollback_called is False

        from db_connector.factory import get_connector
        connector = await get_connector()
        assert await connector.query("SELECT id FROM users WHERE id = 8") == []
        async with async_session() as session:
            backup = await session.get(DbBackup, backup_id)
            assert backup.status == BackupStatus.active


class TestLogsAPI:
    @pytest.mark.asyncio
    async def test_get_logs_paginated(self, client):
        res = await client.get("/api/db_operations/logs?page=1&page_size=10")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_get_logs_filter_by_type(self, client):
        res = await client.get("/api/db_operations/logs?operation_type=INSERT&page_size=5")
        assert res.status_code == 200
        for item in res.json()["items"]:
            assert item["operation_type"] == "INSERT"

    @pytest.mark.asyncio
    async def test_get_logs_filter_by_table(self, client):
        res = await client.get("/api/db_operations/logs?table_name=users&page_size=5")
        assert res.status_code == 200
        for item in res.json()["items"]:
            assert item["table_name"] == "users"
