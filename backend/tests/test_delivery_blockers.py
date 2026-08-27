"""Regression coverage for delivery-blocking security controls."""

import pytest
import asyncio

from utils.crypto import decrypt_if_needed, encrypt, encrypt_if_needed, reencrypt


def test_versioned_encryption_supports_rotation_and_legacy_ciphertext():
    old, new = "old-secret", "new-secret"
    legacy = f"ENC:{encrypt('value', old)}"
    assert decrypt_if_needed(legacy, old) == "value"

    rotated = reencrypt(legacy, old, "2", {"1": old, "2": new})
    assert rotated.startswith("ENC:v2:")
    assert decrypt_if_needed(rotated, old, {"1": old, "2": new}) == "value"


def test_unknown_key_version_is_rejected():
    value = encrypt_if_needed("value", "secret", "7")
    with pytest.raises(ValueError, match="unavailable"):
        decrypt_if_needed(value, "legacy", {"1": "legacy"})


@pytest.mark.asyncio
async def test_identifier_must_exist_in_database_metadata(db_connector):
    schema = await db_connector.get_schema("valid_but_missing")
    assert schema["columns"] == []


@pytest.mark.asyncio
async def test_transaction_rolls_back_all_writes(db_connector):
    with pytest.raises(RuntimeError):
        async with db_connector.transaction() as tx:
            await tx.execute("UPDATE users SET age = :age WHERE id = :id", {"age": 999, "id": 1})
            raise RuntimeError("simulated crash")
    rows = await db_connector.query("SELECT age FROM users WHERE id = 1")
    assert rows[0]["age"] == 28


@pytest.mark.asyncio
async def test_update_that_changes_where_column_rolls_back_by_primary_key(
    db_connector, init_rag_db,
):
    from db_connector.backup import BackupManager

    manager = BackupManager(db_connector)
    async with db_connector.transaction() as tx:
        backup = await manager.create_backup(
            "users", "role = 'admin'", "UPDATE", transaction=tx,
        )
        await tx.execute("UPDATE users SET role = 'former_admin' WHERE role = 'admin'")

    assert not await db_connector.query("SELECT id FROM users WHERE role = 'admin'")
    restored = await manager.rollback(backup.backup_id)
    assert restored == 2
    assert len(await db_connector.query("SELECT id FROM users WHERE role = 'admin'")) == 2


@pytest.mark.asyncio
async def test_rollback_resolves_primary_key_conflict_from_snapshot(
    db_connector, init_rag_db,
):
    from db_connector.backup import BackupManager

    manager = BackupManager(db_connector)
    async with db_connector.transaction() as tx:
        backup = await manager.create_backup("users", "id = 1", "UPDATE", transaction=tx)
        await tx.execute("UPDATE users SET name = 'changed' WHERE id = 1")
    # A conflicting current row with the same PK is replaced by snapshot data.
    await db_connector.execute("UPDATE users SET name = 'conflict' WHERE id = 1")
    await manager.rollback(backup.backup_id)
    row = (await db_connector.query("SELECT name FROM users WHERE id = 1"))[0]
    assert row["name"] == "Alice"


@pytest.mark.asyncio
async def test_concurrent_audit_writers_keep_unique_valid_chain(
    db_connector, init_rag_db,
):
    from audit import verify_audit_chain
    from db_connector.backup import BackupManager
    from models.database import async_session
    from models.schemas import DbOperationLog
    from sqlalchemy import func, select

    manager = BackupManager(db_connector)
    await asyncio.gather(*[
        manager.log_operation("UPDATE", f"UPDATE t SET v={i}", 1, None, "completed")
        for i in range(8)
    ])
    async with async_session() as session:
        result = await session.execute(
            select(func.count(DbOperationLog.sequence), func.count(func.distinct(DbOperationLog.sequence)))
        )
        total, distinct = result.one()
    assert total == distinct
    assert (await verify_audit_chain()).valid is True


@pytest.mark.asyncio
async def test_audit_rows_are_database_immutable(db_connector, init_rag_db):
    from db_connector.backup import BackupManager
    from models.database import async_session
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    log_id = await BackupManager(db_connector).log_operation(
        "UPDATE", "UPDATE users SET age = 1 WHERE id = 1", 1, None, "completed"
    )
    async with async_session() as session:
        with pytest.raises(DatabaseError, match="append-only"):
            await session.execute(
                text("UPDATE db_operation_log SET sql_text='tampered' WHERE id=:id"),
                {"id": log_id},
            )
            await session.commit()
        await session.rollback()
        with pytest.raises(DatabaseError, match="append-only"):
            await session.execute(
                text("DELETE FROM db_operation_log WHERE id=:id"), {"id": log_id}
            )
            await session.commit()
