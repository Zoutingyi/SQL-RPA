"""Real dependency acceptance tests; enabled only in the CI integration job."""

import os
import uuid
import asyncio
import multiprocessing

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_INTEGRATIONS") != "1",
    reason="requires real MySQL and Qdrant services",
)


def _redis_failure_worker(redis_url: str, username: str, attempts: int, result_queue) -> None:
    """Independent process used to prove cross-worker atomic login locking."""
    os.environ["REDIS_URL"] = redis_url

    async def run() -> None:
        import auth
        from config import settings
        settings.redis_url = redis_url
        auth._login_redis = None
        for _ in range(attempts):
            await auth.record_login_failure(username)
        result_queue.put(await auth.login_lock_remaining(username))

    asyncio.run(run())


@pytest.mark.asyncio
async def test_mysql_connection_schema_write_and_rollback():
    from db_connector.mysql_impl import MySQLConnector
    from db_connector.backup import BackupManager
    from models.database import init_db

    conn = MySQLConnector("127.0.0.1", 3306, "sqlrpa", "sqlrpa", "sqlrpa")
    await init_db()
    await conn.connect()
    try:
        await conn.execute("CREATE TABLE IF NOT EXISTS integration_items (id INT PRIMARY KEY, name VARCHAR(100))")
        await conn.execute("DELETE FROM integration_items WHERE id = %s", (1,))
        await conn.execute("INSERT INTO integration_items (id, name) VALUES (%s, %s)", (1, "before"))
        assert "integration_items" in await conn.get_tables()
        assert any(c["name"] == "name" for c in (await conn.get_schema("integration_items"))["columns"])
        with pytest.raises(RuntimeError):
            async with conn.transaction() as tx:
                await tx.execute("UPDATE integration_items SET name = %s WHERE id = %s", ("after", 1))
                raise RuntimeError("force rollback")
        rows = await conn.query("SELECT name FROM integration_items WHERE id = %s", (1,))
        assert rows[0]["name"] == "before"

        manager = BackupManager(conn)
        async with conn.transaction() as tx:
            backup = await manager.create_backup(
                "integration_items", "id = 1", "UPDATE", transaction=tx,
            )
            await tx.execute("UPDATE integration_items SET name = %s WHERE id = %s", ("changed", 1))
        assert await manager.rollback(backup.backup_id) == 1
        rows = await conn.query("SELECT name FROM integration_items WHERE id = %s", (1,))
        assert rows[0]["name"] == "before"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_qdrant_collection_write_and_vector_search():
    from vectordb.qdrant_impl import QdrantVectorDB

    db = QdrantVectorDB(host="127.0.0.1", port=6333, collection_name=f"ci_{uuid.uuid4().hex}")
    await db.ensure_collection(3)
    point_id = str(uuid.uuid4())
    await db.upsert([{"id": point_id, "vector": [1.0, 0.0, 0.0], "payload": {
        "document_id": "doc-1", "text": "integration"
    }}])
    results = await db.search([1.0, 0.0, 0.0], top_k=1)
    assert results and str(results[0].chunk_id) == point_id


@pytest.mark.asyncio
async def test_postgresql_schema_write_transaction_and_backup_rollback():
    from db_connector.postgres_impl import PostgreSQLConnector
    from db_connector.backup import BackupManager
    from models.database import init_db

    await init_db()
    conn = PostgreSQLConnector("127.0.0.1", 5432, "sqlrpa", "sqlrpa", "sqlrpa")
    await conn.connect()
    try:
        await conn.execute("CREATE TABLE IF NOT EXISTS integration_items (id INT PRIMARY KEY, name TEXT)")
        await conn.execute("DELETE FROM integration_items WHERE id=$1", (1,))
        await conn.execute("INSERT INTO integration_items (id,name) VALUES ($1,$2)", (1, "before"))
        assert "integration_items" in await conn.get_tables()
        assert any(c["key"] == "PRI" for c in (await conn.get_schema("integration_items"))["columns"])
        manager = BackupManager(conn)
        async with conn.transaction() as tx:
            backup = await manager.create_backup(
                "integration_items", "id = 1", "UPDATE", transaction=tx,
            )
            await tx.execute("UPDATE integration_items SET name=$1 WHERE id=$2", ("after", 1))
        assert await manager.rollback(backup.backup_id) == 1
        assert (await conn.query("SELECT name FROM integration_items WHERE id=$1", (1,)))[0]["name"] == "before"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_redis_login_failures_are_shared_and_atomic(monkeypatch):
    import auth
    from config import settings

    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:6379/0")
    auth._login_redis = None
    username = f"redis-lock-{uuid.uuid4().hex}"
    await auth.reset_login_failures(username)
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    first_attempts = auth.LOGIN_MAX_ATTEMPTS // 2
    processes = [
        context.Process(target=_redis_failure_worker,
                        args=(settings.redis_url, username, first_attempts, results)),
        context.Process(target=_redis_failure_worker,
                        args=(settings.redis_url, username,
                              auth.LOGIN_MAX_ATTEMPTS - first_attempts, results)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert results.get(timeout=5) >= 0
    assert results.get(timeout=5) >= 0
    auth._login_redis = None
    assert await auth.login_lock_remaining(username) > 0
    await auth.reset_login_failures(username)
    assert await auth.login_lock_remaining(username) == 0


@pytest.mark.asyncio
async def test_postgresql_advisory_lock_preserves_last_platform_admin():
    import asyncio
    from sqlalchemy import func, select, update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from api.auth import _lock_platform_admin_mutation
    from auth import hash_password
    from models.schemas import User, UserRole

    engine = create_async_engine(
        "postgresql+asyncpg://sqlrpa:sqlrpa@127.0.0.1:5432/sqlrpa")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    first = User(id=str(uuid.uuid4()), username=f"pg-lock-a-{suffix}",
        display_name="PG Lock A", password_hash=hash_password("PgLockA-1234"),
        role=UserRole.viewer, is_platform_admin=True, is_active=True)
    second = User(id=str(uuid.uuid4()), username=f"pg-lock-b-{suffix}",
        display_name="PG Lock B", password_hash=hash_password("PgLockB-1234"),
        role=UserRole.viewer, is_platform_admin=True, is_active=True)
    async with sessions() as session:
        await session.execute(update(User).values(is_platform_admin=False))
        session.add_all([first, second])
        await session.commit()

    async def disable(target_id: str) -> bool:
        async with sessions() as session:
            await _lock_platform_admin_mutation(session)
            active = await session.scalar(select(func.count()).select_from(User).where(
                User.is_platform_admin.is_(True), User.is_active.is_(True)))
            if (active or 0) <= 1:
                await session.rollback()
                return False
            target = await session.scalar(select(User).where(
                User.id == target_id).with_for_update())
            target.is_active = False
            await session.commit()
            return True

    try:
        outcomes = await asyncio.gather(disable(first.id), disable(second.id))
        assert sum(outcomes) == 1
        async with sessions() as session:
            remaining = await session.scalar(select(func.count()).select_from(User).where(
                User.is_platform_admin.is_(True), User.is_active.is_(True)))
            assert remaining == 1
    finally:
        await engine.dispose()
