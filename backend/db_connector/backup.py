"""Data backup and rollback manager for database write operations."""

import asyncio
import json
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func as sa_func

from .base import DatabaseConnector
from .sql_utils import validate_user_identifier, validate_where_condition
from config import settings, get_active_encryption_key, get_encryption_keyring
from utils.crypto import decrypt_if_needed, encrypt_if_needed
from models.database import async_session
from models.schemas import DbBackup, DbBackupChunk, DbOperationLog, OperationStatus, BackupStatus
from audit import audit_entry_hash
from auth import get_tenant_id

_audit_lock = asyncio.Lock()


@dataclass
class BackupResult:
    backup_id: str
    table_name: str
    affected_rows: int
    rollback_sql: str
    data_snapshot: dict  # {"columns": [...], "rows": [[...], ...]}


class BackupManager:
    """Manages pre-write data snapshots and rollback operations.

    Uses the target database connector for reading / writing business data,
    and the RAG Agent's SQLAlchemy session for backup metadata and audit logs.
    """

    def __init__(self, connector: DatabaseConnector | None):
        self._connector = connector

    # ── Backup ──

    async def create_backup(
        self, table_name: str, condition: str, operation_type: str,
        transaction=None,
    ) -> BackupResult:
        """Create a data snapshot before a write operation executes.

        Args:
            table_name: The table affected by the write.
            condition: The WHERE condition (without the WHERE keyword).
            operation_type: INSERT / UPDATE / DELETE.
        """
        backup_id = str(uuid.uuid4())

        validate_user_identifier(table_name, "table name")
        validate_where_condition(condition)
        quoted_table = self._connector.quote_identifier(table_name)

        # 1. Query the rows that will be affected
        sql = f'SELECT * FROM {quoted_table} WHERE {condition}'
        if transaction is not None and self._connector.__class__.__name__ in {"MySQLConnector", "PostgreSQLConnector"}:
            sql += " FOR UPDATE"
        reader = transaction or self._connector
        rows = await reader.query(sql)

        schema = await self._connector.get_schema(table_name)
        schema_columns = [column["name"] for column in schema.get("columns", [])]
        columns = list(rows[0].keys()) if rows else schema_columns
        row_values = [list(r.values()) for r in rows]
        primary_keys = [c["name"] for c in schema.get("columns", []) if c.get("key") == "PRI"]
        snapshot = {"columns": columns, "rows": row_values, "primary_keys": primary_keys}

        # 2. Build legacy display SQL and parameterized rollback representation.
        rollback_sql = self._build_legacy_rollback_sql(table_name, columns, row_values)

        # 3. Persist backup record to RAG Agent DB. The snapshot is compressed and
        #    encrypted so business data is not stored as plaintext JSON.
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str, separators=(",", ":"))
        compressed = zlib.compress(snapshot_json.encode("utf-8"))
        if len(compressed) > settings.backup_max_snapshot_bytes:
            raise ValueError("Backup snapshot exceeds BACKUP_MAX_SNAPSHOT_BYTES")
        key_version, active_secret = get_active_encryption_key()
        chunks = [compressed[i:i + settings.backup_chunk_bytes]
                  for i in range(0, len(compressed), settings.backup_chunk_bytes)] or [b""]

        async with async_session() as session:
            used = await session.scalar(select(sa_func.coalesce(sa_func.sum(DbBackupChunk.byte_size), 0)))
            if int(used or 0) + len(compressed) > settings.backup_total_capacity_bytes:
                raise ValueError("Backup capacity exhausted; clean expired snapshots first")
            backup = DbBackup(
                id=backup_id, tenant_id=get_tenant_id(),
                table_name=table_name,
                operation_type=operation_type,
                condition_sql=condition,
                rollback_sql=rollback_sql,
                data_snapshot="CHUNKED:v1",
                affected_rows=len(rows),
                status=BackupStatus.active,
                created_at=datetime.now(timezone.utc),
                expired_at=datetime.now(timezone.utc) + timedelta(days=settings.backup_retention_days),
            )
            session.add(backup)
            for index, chunk in enumerate(chunks):
                session.add(DbBackupChunk(
                    id=str(uuid.uuid4()), tenant_id=get_tenant_id(),
                    backup_id=backup_id, chunk_index=index,
                    data=encrypt_if_needed(chunk.hex(), active_secret, key_version), byte_size=len(chunk),
                ))
            await session.commit()

        return BackupResult(
            backup_id=backup_id, table_name=table_name,
            affected_rows=len(rows), rollback_sql=rollback_sql,
            data_snapshot=snapshot,
        )

    # ── Rollback ──

    async def rollback(self, backup_id: str) -> int:
        """Roll back to a previous backup point. Returns the number of restored rows."""
        async with async_session() as session:
            result = await session.execute(
                select(DbBackup).where(
                    DbBackup.id == backup_id,
                    DbBackup.tenant_id == get_tenant_id(),
                    DbBackup.status == BackupStatus.active,
                )
            )
            backup = result.scalar_one_or_none()
            if not backup:
                raise ValueError(f"Backup not found or already expired: {backup_id}")

            rollback_sql = backup.rollback_sql
            table_name = backup.table_name

            if not rollback_sql and backup.operation_type != "ROLLBACK":
                raise ValueError(f"Backup has no rollback SQL: {backup_id}")

            snapshot = await self._decode_snapshot(backup)
            validate_user_identifier(table_name, "table name")
            quoted_table = self._connector.quote_identifier(table_name)
            columns = snapshot.get("columns", [])
            rows = snapshot.get("rows", [])
            primary_keys = snapshot.get("primary_keys", [])
            if not rows and backup.operation_type == "ROLLBACK":
                validate_where_condition(backup.condition_sql)
                async with self._connector.transaction() as tx:
                    deleted = await tx.execute(
                        f"DELETE FROM {quoted_table} WHERE {backup.condition_sql}"
                    )
                backup.status = BackupStatus.rolled_back
                await session.commit()
                return max(deleted, 0)
            if not primary_keys:
                raise ValueError("Rollback requires a primary key in the backup snapshot")

            indices = {name: idx for idx, name in enumerate(columns)}
            if any(key not in indices for key in primary_keys):
                raise ValueError("Backup snapshot is missing primary-key values")
            if self._connector.__class__.__name__ == "PostgreSQLConnector":
                predicates = " AND ".join(
                    f"{self._connector.quote_identifier(key)} = ${index}"
                    for index, key in enumerate(primary_keys, 1)
                )
            else:
                predicates = " AND ".join(
                    f"{self._connector.quote_identifier(key)} = {self._connector.placeholder()}"
                    for key in primary_keys
                )
            delete_sql = f"DELETE FROM {quoted_table} WHERE {predicates}"
            insert_sql, insert_rows = self._build_parameterized_insert(table_name, columns, rows)

            # Restore each snapshot identity. Never reuse the original WHERE,
            # which may no longer match after an UPDATE changes filtered fields.
            async with self._connector.transaction() as tx:
                for row in rows:
                    await tx.execute(delete_sql, tuple(row[indices[key]] for key in primary_keys))
                if insert_rows:
                    await tx.execute_many(insert_sql, insert_rows)

            # 3. Mark backup as rolled back
            backup.status = BackupStatus.rolled_back
            await session.commit()

            return len(rows)

    # ── Audit log ──

    async def log_operation(
        self, operation_type: str, sql_text: str, affected_rows: int,
        backup_id: str | None, status: str, table_name: str = "",
        executed_by: str = "agent", error_message: str | None = None,
        submitted_by: str | None = None, approved_by: str | None = None,
        reviewer_note: str | None = None,
        review_id: str | None = None,
    ) -> str:
        """Write an entry to the operation audit log. Returns the log entry ID."""
        try:
            op_status = OperationStatus(status)
        except ValueError:
            op_status = OperationStatus.completed

        async with _audit_lock:
            for attempt in range(5):
                log_id = str(uuid.uuid4())
                async with async_session() as session:
                    if session.get_bind().dialect.name == "sqlite":
                        # Cross-process writer lock; the Python lock alone only
                        # protects one worker.
                        await session.execute(text("BEGIN IMMEDIATE"))
                    latest = select(DbOperationLog).order_by(
                        DbOperationLog.sequence.desc()
                    ).limit(1)
                    if session.get_bind().dialect.name != "sqlite":
                        latest = latest.with_for_update()
                    result = await session.execute(latest)
                    previous = result.scalar_one_or_none()
                    last_seq = previous.sequence if previous else 0
                    sequence = last_seq + 1
                    prev_hash = previous.entry_hash if previous else None

                    now = datetime.now(timezone.utc).replace(tzinfo=None)

                    log = DbOperationLog(
                        id=log_id, tenant_id=get_tenant_id(),
                        operation_type=operation_type,
                        sql_text=sql_text,
                        affected_rows=affected_rows,
                        table_name=table_name,
                        backup_id=backup_id,
                        review_id=review_id,
                        status=op_status,
                        error_message=error_message,
                        executed_by=executed_by,
                        submitted_by=submitted_by,
                        approved_by=approved_by,
                        reviewer_note=reviewer_note,
                        sequence=sequence,
                        prev_hash=prev_hash,
                        entry_hash="pending",
                        created_at=now,
                    )
                    log.entry_hash = audit_entry_hash(log)
                    session.add(log)
                    try:
                        await session.commit()
                        return log_id
                    except IntegrityError:
                        await session.rollback()
                        if attempt == 4:
                            raise

    # ── Maintenance ──

    async def cleanup_expired_backups(self) -> int:
        """Mark all expired backups. Returns the count of cleaned records."""
        async with async_session() as session:
            result = await session.execute(
                select(DbBackup).where(
                    DbBackup.status == BackupStatus.active,
                    DbBackup.expired_at < datetime.now(timezone.utc),
                )
            )
            expired = result.scalars().all()
            for b in expired:
                b.status = BackupStatus.expired
                await session.execute(delete(DbBackupChunk).where(DbBackupChunk.backup_id == b.id))
            await session.commit()
            return len(expired)

    # ── Private helpers ──

    def _build_legacy_rollback_sql(self, table_name: str, columns: list[str], rows: list[list]) -> str:
        """Build a readable INSERT statement for compatibility/display.

        Execution no longer uses this string; rollback uses parameterized
        executemany() inside a transaction.
        """
        if not rows:
            return ""

        col_list = ", ".join(self._connector.quote_identifier(c) for c in columns)
        quoted_table = self._connector.quote_identifier(table_name)

        values_parts = []
        for row in rows:
            vals = []
            for val in row:
                if val is None:
                    vals.append("NULL")
                elif isinstance(val, (int, float)):
                    vals.append(str(val))
                else:
                    escaped = str(val).replace("'", "''")
                    vals.append(f"'{escaped}'")
            values_parts.append(f"({', '.join(vals)})")

        return f'INSERT INTO {quoted_table} ({col_list})\nVALUES\n{", ".join(values_parts)};'

    def _build_parameterized_insert(
        self, table_name: str, columns: list[str], rows: list[list]
    ) -> tuple[str, list[tuple]]:
        if not rows or not columns:
            return "", []

        quoted_table = self._connector.quote_identifier(table_name)
        quoted_columns = ", ".join(self._connector.quote_identifier(c) for c in columns)
        if self._connector.__class__.__name__ == "PostgreSQLConnector":
            placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
        else:
            placeholders = ", ".join([self._connector.placeholder()] * len(columns))
        sql = f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})"
        return sql, [tuple(row) for row in rows]

    async def _decode_snapshot(self, backup: DbBackup) -> dict:
        stored = backup.data_snapshot
        if stored == "CHUNKED:v1":
            async with async_session() as session:
                chunks = (await session.execute(
                    select(DbBackupChunk).where(DbBackupChunk.backup_id == backup.id)
                    .order_by(DbBackupChunk.chunk_index)
                )).scalars().all()
            if not chunks:
                raise ValueError("Backup snapshot chunks are missing")
            compressed = b"".join(bytes.fromhex(decrypt_if_needed(
                chunk.data, settings.secret_key, get_encryption_keyring()
            )) for chunk in chunks)
            return json.loads(zlib.decompress(compressed).decode("utf-8"))
        plaintext = decrypt_if_needed(stored, settings.secret_key, get_encryption_keyring())
        if stored.startswith("ENC:"):
            plaintext = bytes.fromhex(plaintext)
            plaintext = zlib.decompress(plaintext).decode("utf-8")
        elif stored.startswith("{") or stored.startswith("["):
            # Legacy plaintext snapshots.
            return json.loads(stored)
        else:
            raise ValueError("Unsupported backup snapshot format")
        return json.loads(plaintext)
